"""Audit event recording helpers."""
from __future__ import annotations

import logging

from apps.audit.models import AuditLog

logger = logging.getLogger("apps.audit")

_ALLOWED_METADATA_KEYS = {
    "role",
    "previous_role",
    "environment",
    "status",
    "previous_status",
    "name",
    "slug",
    "version",
    "assistant_id",
    "api_key_id",
    "document_id",
    "knowledge_base_id",
    "conversation_id",
    "reason",
    "count",
    "changes",
    "email",
    "provider",
    "model",
    "key_last4",
    "channel_id",
}


def _sanitize(metadata: dict | None) -> dict:
    """Keep only whitelisted keys and coerce values to JSON-safe scalars.

    Prevents accidental leakage of prompts, secrets or arbitrary payloads into
    the audit table.
    """
    if not metadata:
        return {}
    clean: dict = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, (list, tuple)):
            clean[key] = [str(item)[:100] for item in value[:20]]
        elif isinstance(value, dict):
            clean[key] = {str(k)[:50]: str(v)[:100] for k, v in list(value.items())[:20]}
        else:
            clean[key] = str(value)[:100]
    return clean


def record_event(
    *,
    action: str,
    organization=None,
    actor_user=None,
    api_key=None,
    target_type: str = "",
    target_id: str = "",
    metadata: dict | None = None,
    request=None,
) -> AuditLog | None:
    """Persist an audit row.

    Audit failures are logged but never propagate: losing an audit row must not
    take down a customer-facing request. Alerting on the log line is the
    operational control for this trade-off.
    """
    if request is not None:
        request_id = getattr(request, "request_id", "")
    else:
        from apps.common.logging import get_request_id

        request_id = get_request_id()

    ip_hash = ""
    if request is not None:
        from apps.common.crypto import sha256_hex

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
        ip_hash = sha256_hex(ip)[:32] if ip else ""

    try:
        return AuditLog.objects.create(
            organization=organization,
            actor_user=actor_user if getattr(actor_user, "pk", None) else None,
            api_key=api_key,
            action=action,
            target_type=target_type[:64],
            target_id=str(target_id)[:64],
            metadata=_sanitize(metadata),
            request_id=request_id or "",
            ip_hash=ip_hash,
        )
    except Exception:
        logger.exception("Failed to write audit log action=%s", action)
        return None

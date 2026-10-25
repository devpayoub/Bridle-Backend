"""API key domain services: creation, rotation, revocation, verification."""
from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.api_keys.models import APIKey
from apps.common.crypto import hmac_digest, verify_digest
from apps.common.exceptions import APIError

logger = logging.getLogger("apps.api_keys")

MAX_KEYS_PER_ORGANIZATION = None  # enforced through billing entitlements

# Public key format: "bb-" + 47 base62 characters = 50 characters in total.
KEY_PREFIX = "bb-"
KEY_RANDOM_LENGTH = 47
# First 11 characters ("bb-" + 8) are stored in clear for O(1) lookup; the rest is
# only ever held as an HMAC digest.
KEY_LOOKUP_LENGTH = 11
KEY_ALPHABET = string.ascii_letters + string.digits

SCOPE_CHOICES = [
    "chat:write",
    "assistant:read",
    "assistant:write",
    "policy:read",
    "policy:write",
    "policy:evaluate",
    "knowledge:read",
    "knowledge:write",
    "conversation:read",
    "usage:read",
    "apikey:read",
    "*",
]


def _build_secret() -> tuple[str, str]:
    """Return ``(lookup_prefix, full_key)``.

    The environment is a property of the record, not of the string: a leaked key
    tells an attacker nothing about whether it is live or test, and rotating one
    does not change the shape clients see.
    """
    body = "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_RANDOM_LENGTH))
    full_key = f"{KEY_PREFIX}{body}"
    return full_key[:KEY_LOOKUP_LENGTH], full_key


@transaction.atomic
def create_api_key(
    *,
    organization,
    name: str,
    environment: str = APIKey.Environment.DEVELOPMENT,
    scopes: list[str] | None = None,
    assistant=None,
    created_by=None,
    expires_at: datetime | None = None,
) -> tuple[APIKey, str]:
    """Create a key and return ``(record, plaintext)``. The plaintext is never stored."""
    if assistant is not None and assistant.organization_id != organization.id:
        raise APIError("The assistant does not belong to this organization.", code="invalid_assistant")

    prefix, full_key = _build_secret()
    record = APIKey.objects.create(
        organization=organization,
        name=name,
        prefix=prefix,
        key_hash=hmac_digest(full_key),
        environment=environment,
        scopes=scopes or ["chat:write"],
        assistant=assistant,
        created_by=created_by,
        expires_at=expires_at,
    )
    logger.info("API key created key_id=%s environment=%s", record.id, environment)
    return record, full_key


@transaction.atomic
def rotate_api_key(api_key: APIKey, *, actor=None) -> tuple[APIKey, str]:
    """Revoke ``api_key`` and issue a replacement with identical configuration."""
    if api_key.is_revoked:
        raise APIError("This key has already been revoked.", code="key_revoked")

    new_key, plaintext = create_api_key(
        organization=api_key.organization,
        name=f"{api_key.name} (rotated)",
        environment=api_key.environment,
        scopes=list(api_key.scopes or []),
        assistant=api_key.assistant,
        created_by=actor or api_key.created_by,
        expires_at=api_key.expires_at,
    )
    new_key.rotated_from = api_key
    new_key.save(update_fields=["rotated_from", "updated_at"])

    api_key.revoked_at = timezone.now()
    api_key.save(update_fields=["revoked_at", "updated_at"])
    logger.info("API key rotated old=%s new=%s", api_key.id, new_key.id)
    return new_key, plaintext


def revoke_api_key(api_key: APIKey) -> APIKey:
    if api_key.is_revoked:
        return api_key
    api_key.revoked_at = timezone.now()
    api_key.save(update_fields=["revoked_at", "updated_at"])
    logger.info("API key revoked key_id=%s", api_key.id)
    return api_key


_DUMMY_DIGEST = "0" * 64


def _lookup_prefix(raw_key: str) -> str | None:
    """Extract the stored lookup prefix, or ``None`` if the format is not recognised."""
    if not raw_key:
        return None
    if raw_key.startswith(KEY_PREFIX):
        expected = len(KEY_PREFIX) + KEY_RANDOM_LENGTH
        if len(raw_key) != expected:
            return None
        return raw_key[:KEY_LOOKUP_LENGTH]
    # Legacy format from earlier releases (sk_<env>_<hex>.<secret>) keeps working so
    # existing integrations are not broken by the switch to bb- keys.
    if "." in raw_key:
        return raw_key.partition(".")[0]
    return None


def authenticate_api_key(raw_key: str) -> APIKey:
    """Verify a presented key or raise ``AuthenticationFailed``.

    Errors are intentionally indistinguishable to avoid building an oracle.
    """
    from rest_framework.exceptions import AuthenticationFailed

    invalid = AuthenticationFailed("Invalid API key.")

    prefix = _lookup_prefix(raw_key)
    if prefix is None:
        raise invalid

    record = APIKey.objects.select_related("organization", "assistant").filter(prefix=prefix).first()

    if record is None:
        # Perform a comparison anyway so a missing prefix is not measurably faster.
        verify_digest(raw_key, _DUMMY_DIGEST)
        raise invalid

    if not verify_digest(raw_key, record.key_hash):
        raise invalid

    if record.is_revoked or record.is_expired:
        raise AuthenticationFailed("This API key is no longer valid.")

    if not record.organization.is_active:
        raise AuthenticationFailed("This API key is no longer valid.")

    touch_last_used(record)
    return record


def touch_last_used(record: APIKey, *, min_interval_seconds: int = 60) -> None:
    """Update ``last_used_at`` at most once per interval to limit write load."""
    now = timezone.now()
    if record.last_used_at and (now - record.last_used_at).total_seconds() < min_interval_seconds:
        return
    try:
        APIKey.objects.filter(pk=record.pk).update(last_used_at=now, updated_at=now)
        record.last_used_at = now
    except Exception:  # pragma: no cover - never fail auth because of telemetry
        logger.warning("Unable to update API key last_used_at", exc_info=True)


def default_ttl_days() -> int | None:
    value = getattr(settings, "API_KEY_DEFAULT_TTL_DAYS", None)
    return int(value) if value else None

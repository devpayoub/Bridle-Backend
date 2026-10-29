"""Channel management: connect, disconnect, test, rotate.

Network calls to the provider never happen inside a transaction. The row is
created first (so we have an id and a webhook URL), then registered, then
updated — a failed registration leaves a visible ``ERROR`` row instead of a
half-written one.
"""
from __future__ import annotations

import logging
import re
import time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.assistants.models import Assistant
from apps.audit.models import AuditLog
from apps.audit.services import record_event
from apps.channels.exceptions import ChannelRegistrationFailed
from apps.channels.models import Channel
from apps.channels.registry import get_adapter
from apps.channels.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramUnauthorized,
)
from apps.common.crypto import generate_secret
from apps.common.exceptions import APIError, Conflict, TenantAccessDenied

logger = logging.getLogger("apps.channels")

# 8123456789:AAH... — the id is numeric, the secret is 35+ URL-safe chars.
BOT_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")


def webhook_url_for(channel: Channel) -> str:
    """The public URL the provider posts to. Empty when the base URL is unset."""
    base = settings.CHANNELS_PUBLIC_BASE_URL
    key = channel.get_webhook_key()
    if not base or not key:
        return ""
    return f"{base}/webhooks/{channel.type.lower()}/{channel.id}/{key}/"


def _assert_assistant(organization, assistant_id) -> Assistant:
    assistant = Assistant.objects.filter(id=assistant_id, organization=organization).first()
    if assistant is None:
        # 404 rather than 403: never confirm that a foreign id exists.
        raise TenantAccessDenied("Assistant not found.")
    if not assistant.accepts_requests:
        raise Conflict("This assistant is not active yet.", code="assistant_not_available")
    return assistant


def connect_channel(*, tenant, assistant_id, bot_token: str, name: str = "", request=None) -> Channel:
    """Validate the token, register the webhook, return an ACTIVE channel."""
    bot_token = (bot_token or "").strip()
    if not BOT_TOKEN_RE.match(bot_token):
        raise APIError("That does not look like a Telegram bot token.", code="invalid_bot_token")

    organization = tenant.organization
    assistant = _assert_assistant(organization, assistant_id)

    try:
        bot = TelegramClient(bot_token).get_me()
    except TelegramUnauthorized:
        raise APIError("Telegram rejected that bot token.", code="invalid_bot_token") from None
    except TelegramError as exc:
        raise ChannelRegistrationFailed(str(exc)) from None

    if not bot.id:
        raise APIError("Telegram returned an empty bot identity.", code="invalid_bot_token")

    if Channel.objects.filter(
        type=Channel.Type.TELEGRAM, external_id=bot.id, status=Channel.Status.ACTIVE
    ).exists():
        # Deliberately vague: the caller may not own the other workspace.
        raise Conflict("This bot is already connected.", code="channel_unavailable")

    channel = Channel(
        organization=organization,
        assistant=assistant,
        type=Channel.Type.TELEGRAM,
        name=(name or bot.username or "Telegram")[:120],
        status=Channel.Status.PENDING,
        external_id=bot.id,
        external_username=bot.username,
        created_by=getattr(tenant, "actor_user", None),
    )
    channel.set_credentials(bot_token)
    channel.set_webhook_key(generate_secret(32))
    try:
        channel.save()
    except IntegrityError:
        raise Conflict("This bot is already connected.", code="channel_unavailable") from None

    secret = generate_secret(32)
    url = webhook_url_for(channel)
    if not url:
        channel.delete()
        raise APIError(
            "The API has no public URL yet, so Telegram cannot reach the webhook.",
            code="channel_misconfigured",
            extra={"hint": "Set CHANNELS_PUBLIC_BASE_URL to a public HTTPS origin and restart the API."},
        )
    try:
        get_adapter(channel.type).register(channel, url, secret)
    except TelegramError as exc:
        channel.mark_error(exc.code)
        channel.save(update_fields=["status", "webhook_error", "updated_at"])
        record_event(
            action=AuditLog.Action.CHANNEL_ERROR,
            organization=organization,
            actor_user=getattr(tenant, "actor_user", None),
            target_type="channel",
            target_id=channel.id,
            metadata={"channel_id": str(channel.id), "reason": exc.code, "provider": "telegram"},
            request=request,
        )
        raise ChannelRegistrationFailed(str(exc)) from None

    channel.set_webhook_secret(secret)
    channel.mark_active()
    channel.save(update_fields=["webhook_secret_digest", "status", "webhook_registered_at", "webhook_error", "disabled_at", "updated_at"])

    record_event(
        action=AuditLog.Action.CHANNEL_CONNECTED,
        organization=organization,
        actor_user=getattr(tenant, "actor_user", None),
        target_type="channel",
        target_id=channel.id,
        metadata={
            "channel_id": str(channel.id),
            "assistant_id": str(assistant.id),
            "provider": "telegram",
            "key_last4": channel.credentials_last4,
        },
        request=request,
    )
    return channel


def disconnect_channel(*, channel: Channel, tenant, request=None) -> Channel:
    """Stop delivery and erase every secret. Conversations are tenant data and stay."""
    get_adapter(channel.type).deregister(channel)

    channel.status = Channel.Status.DISABLED
    channel.disabled_at = timezone.now()
    channel.credentials_ciphertext = ""
    channel.credentials_last4 = ""
    channel.webhook_key_ciphertext = ""
    channel.webhook_secret_digest = ""
    channel.save(
        update_fields=[
            "status",
            "disabled_at",
            "credentials_ciphertext",
            "credentials_last4",
            "webhook_key_ciphertext",
            "webhook_secret_digest",
            "updated_at",
        ]
    )

    record_event(
        action=AuditLog.Action.CHANNEL_DISCONNECTED,
        organization=channel.organization,
        actor_user=getattr(tenant, "actor_user", None),
        target_type="channel",
        target_id=channel.id,
        metadata={"channel_id": str(channel.id), "provider": channel.type.lower()},
        request=request,
    )
    return channel


def test_channel(channel: Channel) -> dict:
    """Probe the provider. Always returns a payload — the console renders both outcomes."""
    started = time.monotonic()
    try:
        bot = TelegramClient(channel.get_credentials()).get_me()
    except TelegramUnauthorized:
        return {"ok": False, "error_code": "invalid_bot_token", "error": "Telegram rejected the bot token."}
    except TelegramError as exc:
        return {"ok": False, "error_code": exc.code, "error": str(exc)}
    return {
        "ok": True,
        "bot_id": bot.id,
        "bot_username": bot.username,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def rotate_webhook(*, channel: Channel, tenant, request=None) -> Channel:
    """Issue a new path key and header secret, then re-register.

    The recovery path when a webhook URL leaks: the old URL stops resolving the
    moment the new key is stored.
    """
    channel.set_webhook_key(generate_secret(32))
    secret = generate_secret(32)
    url = webhook_url_for(channel)

    try:
        get_adapter(channel.type).register(channel, url, secret)
    except TelegramError as exc:
        channel.mark_error(exc.code)
        channel.save(update_fields=["webhook_key_ciphertext", "status", "webhook_error", "updated_at"])
        raise ChannelRegistrationFailed(str(exc)) from None

    channel.set_webhook_secret(secret)
    channel.mark_active()
    channel.save(
        update_fields=[
            "webhook_key_ciphertext",
            "webhook_secret_digest",
            "status",
            "webhook_registered_at",
            "webhook_error",
            "disabled_at",
            "updated_at",
        ]
    )
    record_event(
        action=AuditLog.Action.CHANNEL_UPDATED,
        organization=channel.organization,
        actor_user=getattr(tenant, "actor_user", None),
        target_type="channel",
        target_id=channel.id,
        metadata={"channel_id": str(channel.id), "reason": "webhook_rotated"},
        request=request,
    )
    return channel


def update_channel(*, channel: Channel, data: dict, tenant, request=None) -> Channel:
    fields: list[str] = []
    if "name" in data and data["name"]:
        channel.name = str(data["name"])[:120]
        fields.append("name")
    if data.get("assistant_id"):
        assistant = _assert_assistant(channel.organization, data["assistant_id"])
        channel.assistant = assistant
        fields.append("assistant")

    if fields:
        try:
            channel.save(update_fields=[*fields, "updated_at"])
        except IntegrityError:
            raise Conflict("This assistant already has an active channel.", code="channel_unavailable") from None
        record_event(
            action=AuditLog.Action.CHANNEL_UPDATED,
            organization=channel.organization,
            actor_user=getattr(tenant, "actor_user", None),
            target_type="channel",
            target_id=channel.id,
            metadata={"channel_id": str(channel.id), "changes": fields},
            request=request,
        )
    return channel

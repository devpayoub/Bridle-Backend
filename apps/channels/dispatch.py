"""Turning an inbound event into an answer.

This is the only place a channel touches the chat pipeline. It builds a synthetic
tenant, resolves the thread, applies the abuse limits, calls
``handle_chat_request`` and sends whatever came back — including refusals, which
are normal answers, not errors.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.channels.adapters.base import IgnoredUpdate
from apps.channels.models import Channel, ChannelEvent, ChannelIdentity, ChannelThread
from apps.channels.registry import get_adapter
from apps.channels.telegram.formatting import render_reply
from apps.common.exceptions import (
    APIError,
    AssistantNotAvailable,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
)
from apps.conversations.chat import handle_chat_request
from apps.conversations.models import Conversation
from apps.usage.rate_limit import enforce_rate_limit

logger = logging.getLogger("apps.channels.dispatch")

SLOW_DOWN = "You're sending messages a bit fast — give me a moment and try again."
UNAVAILABLE = "This assistant is temporarily unavailable. Please try again later."
NOT_ACCEPTING = "This assistant isn't accepting messages right now."
GENERIC = "Something went wrong on my side. Please try again in a moment."


@dataclass(frozen=True)
class ChannelTenant:
    """Just enough of a tenant for ``handle_chat_request``.

    ``rate_limit_scope`` keeps bot traffic out of the console/API chat bucket.
    """

    organization: object
    api_key: None = None
    rate_limit_scope: str = "chat.channel"

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def can(self, capability: str) -> bool:
        return False


def _chat_id_from(payload: dict) -> str:
    message = payload.get("message") or {}
    chat = message.get("chat") or {}
    return str(chat.get("id", ""))


def _finish(event: ChannelEvent, status: str, *, error_code: str = "", reply_ids: list[str] | None = None) -> None:
    ChannelEvent.objects.filter(pk=event.pk).update(
        status=status,
        error_code=error_code[:48],
        reply_message_ids=reply_ids or [],
        processed_at=timezone.now(),
    )


def _send(adapter, channel: Channel, thread_id: str, parts: list[str], *, reply_to: str | None = None) -> list[str]:
    if not thread_id or not parts:
        return []
    # Telegram allows roughly one message per second per chat.
    enforce_rate_limit("channel.send", f"ch:{channel.id}:{thread_id}")
    return adapter.send_text(channel, thread_id, parts, reply_to=reply_to)


def _canned(adapter, channel: Channel, thread_id: str, text: str) -> list[str]:
    try:
        return _send(adapter, channel, thread_id, [text])
    except Exception:  # noqa: BLE001 - a canned reply must never break the flow
        logger.warning("channel.dispatch.canned_failed channel=%s", channel.id)
        return []


def resolve_or_create_thread(channel: Channel, inbound, identity: ChannelIdentity) -> ChannelThread:
    """Map the external chat to a conversation, creating either when needed.

    The conversation is created here rather than left to the chat service: its id
    is part of the idempotency fingerprint, so it must be stable across retries.
    """
    with transaction.atomic():
        thread = (
            ChannelThread.objects.select_for_update(of=("self",))
            .select_related("conversation")
            .filter(channel=channel, external_thread_id=inbound.external_thread_id)
            .first()
        )
        if thread is None:
            try:
                with transaction.atomic():
                    thread = ChannelThread.objects.create(
                        organization=channel.organization,
                        channel=channel,
                        identity=identity,
                        external_thread_id=inbound.external_thread_id,
                    )
            except IntegrityError:
                thread = (
                    ChannelThread.objects.select_for_update(of=("self",))
                    .select_related("conversation")
                    .get(channel=channel, external_thread_id=inbound.external_thread_id)
                )

        conversation = thread.conversation
        if conversation is None or conversation.status == Conversation.Status.ANONYMIZED:
            # Retention anonymised it: start a fresh one instead of failing the user.
            conversation = Conversation.objects.create(
                organization=channel.organization,
                assistant=channel.assistant,
                external_user_id=inbound.external_user_id,
                config_version=channel.assistant.config_version,
                retention_expires_at=timezone.now()
                + timedelta(days=getattr(settings, "CONVERSATION_RETENTION_DAYS", 365)),
                metadata={"channel_type": channel.type.lower(), "channel_id": str(channel.id)},
            )
            thread.conversation = conversation
            thread.save(update_fields=["conversation", "updated_at"])

        return thread


def handle_inbound_event(event_id: str, payload: dict) -> None:
    """Process one inbound update. Raises Telegram errors so the task can retry."""
    event = (
        ChannelEvent.objects.select_related("channel__organization", "channel__assistant")
        .filter(pk=event_id)
        .first()
    )
    if event is None:
        return
    if event.status in {ChannelEvent.Status.REPLIED, ChannelEvent.Status.IGNORED, ChannelEvent.Status.SENDING}:
        return

    claimed = ChannelEvent.objects.filter(
        pk=event.pk, status__in=[ChannelEvent.Status.RECEIVED, ChannelEvent.Status.FAILED]
    ).update(status=ChannelEvent.Status.PROCESSING, attempts=F("attempts") + 1)
    if not claimed:
        # Another worker owns it.
        return
    event.refresh_from_db()

    channel = event.channel
    chat_id = _chat_id_from(payload)

    if channel.status != Channel.Status.ACTIVE:
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="channel_inactive")

    # The synthetic tenant skips resolve_tenant, which is where suspension is
    # enforced — so check it here or a suspended workspace keeps burning quota.
    if channel.organization.status != "ACTIVE":
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="organization_inactive")

    adapter = get_adapter(channel.type)

    try:
        inbound = adapter.parse_update(channel, payload)
    except IgnoredUpdate as exc:
        if exc.user_message:
            _canned(adapter, channel, chat_id, exc.user_message)
        return _finish(event, ChannelEvent.Status.IGNORED, error_code=exc.reason)

    identity, _ = ChannelIdentity.objects.get_or_create(
        channel=channel,
        external_user_id=inbound.external_user_id,
        defaults={"organization": channel.organization},
    )
    if identity.is_blocked:
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="identity_blocked")
    ChannelIdentity.objects.filter(pk=identity.pk).update(last_seen_at=timezone.now())

    try:
        enforce_rate_limit("channel.chat.identity", f"ch:{channel.id}:u:{inbound.external_user_id}")
        enforce_rate_limit("channel.chat.identity.daily", f"ch:{channel.id}:u:{inbound.external_user_id}")
        enforce_rate_limit("channel.chat.channel.daily", f"ch:{channel.id}")
    except RateLimitExceeded:
        _canned(adapter, channel, inbound.external_thread_id, SLOW_DOWN)
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="rate_limited")

    thread = resolve_or_create_thread(channel, inbound, identity)

    try:
        outcome = handle_chat_request(
            tenant=ChannelTenant(organization=channel.organization),
            assistant_id=channel.assistant_id,
            message=inbound.text,
            conversation_id=thread.conversation_id,
            metadata={
                "external_user_id": inbound.external_user_id,
                "channel_type": channel.type.lower(),
                "channel_id": str(channel.id),
            },
            idempotency_key=f"tg:{channel.id.hex}:{event.update_id}",
            request_id=f"tg-{event.id.hex[:16]}",
        )
    except RateLimitExceeded:
        _canned(adapter, channel, inbound.external_thread_id, SLOW_DOWN)
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="rate_limited")
    except QuotaExceeded:
        _canned(adapter, channel, inbound.external_thread_id, UNAVAILABLE)
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="quota_exceeded")
    except AssistantNotAvailable:
        _canned(adapter, channel, inbound.external_thread_id, NOT_ACCEPTING)
        return _finish(event, ChannelEvent.Status.IGNORED, error_code="assistant_not_available")
    except ProviderUnavailable:
        _canned(adapter, channel, inbound.external_thread_id, UNAVAILABLE)
        return _finish(event, ChannelEvent.Status.FAILED, error_code="provider_unavailable")
    except APIError as exc:
        code = getattr(exc, "default_code", "api_error")
        if code == "idempotency_in_progress":
            # A concurrent attempt is mid-flight; leave the row to it.
            return
        logger.warning("channel.dispatch.api_error channel=%s code=%s", channel.id, code)
        _canned(adapter, channel, inbound.external_thread_id, GENERIC)
        return _finish(event, ChannelEvent.Status.FAILED, error_code=code)
    except Exception:  # noqa: BLE001 - never leak a traceback to the user
        logger.exception("channel.dispatch.unexpected channel=%s", channel.id)
        _canned(adapter, channel, inbound.external_thread_id, GENERIC)
        return _finish(event, ChannelEvent.Status.FAILED, error_code="internal_error")

    parts = render_reply(outcome.payload)

    # Claim the send before the HTTP call: a redelivered task must not send twice.
    ChannelEvent.objects.filter(pk=event.pk).update(status=ChannelEvent.Status.SENDING)
    try:
        reply_ids = _send(
            adapter,
            channel,
            inbound.external_thread_id,
            parts,
            reply_to=inbound.external_message_id,
        )
    except Exception as exc:  # noqa: BLE001 - re-raised for the task to classify
        ChannelEvent.objects.filter(pk=event.pk).update(
            status=ChannelEvent.Status.FAILED, error_code=getattr(exc, "code", "send_failed")[:48]
        )
        raise

    now = timezone.now()
    _finish(event, ChannelEvent.Status.REPLIED, reply_ids=reply_ids)
    Channel.objects.filter(pk=channel.pk).update(last_event_at=now)
    ChannelThread.objects.filter(pk=thread.pk).update(last_message_at=now)

"""Normalising a Telegram update.

Everything this module rejects is rejected **before** the policy engine and the
provider, so an unsupported message costs nothing and produces no answer.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.channels.adapters.base import IgnoredUpdate, InboundMessage

_UNSUPPORTED_KEYS = (
    "photo",
    "document",
    "voice",
    "video",
    "video_note",
    "audio",
    "sticker",
    "animation",
    "location",
    "contact",
    "poll",
    "dice",
    "venue",
)

TEXT_ONLY_REPLY = "I can only read text messages. Could you type your question?"
TOO_LONG_REPLY = "That message is too long for me to read. Could you shorten it?"


def _from_epoch(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def parse_update(payload: dict) -> InboundMessage:
    """Return a normalised message, or raise ``IgnoredUpdate`` with a reason code."""
    if not isinstance(payload, dict):
        raise IgnoredUpdate("malformed_update")
    if "edited_message" in payload:
        raise IgnoredUpdate("edited_message")

    message = payload.get("message")
    if not isinstance(message, dict):
        raise IgnoredUpdate("not_a_message")

    sender = message.get("from") or {}
    if sender.get("is_bot"):
        raise IgnoredUpdate("bot_sender")

    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        # Groups need mention detection and a different reply policy.
        raise IgnoredUpdate("group_chat")

    forwarded = message.get("forward_origin") or message.get("forward_from") or message.get("forward_date")
    if forwarded and not settings.CHANNELS_TELEGRAM_ALLOW_FORWARDS:
        raise IgnoredUpdate("forwarded_message", user_message=TEXT_ONLY_REPLY)

    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        if any(key in message for key in _UNSUPPORTED_KEYS) or message.get("caption"):
            raise IgnoredUpdate("unsupported_content", user_message=TEXT_ONLY_REPLY)
        raise IgnoredUpdate("empty_message")

    sent_at = _from_epoch(message.get("date"))
    if sent_at is not None:
        age = (timezone.now() - sent_at).total_seconds()
        if age > settings.CHANNELS_MAX_UPDATE_AGE_SECONDS:
            # Replay protection after the dedupe rows are purged.
            raise IgnoredUpdate("stale_update")

    text = text.strip()
    if len(text) > settings.CHAT_MAX_MESSAGE_CHARS:
        raise IgnoredUpdate("message_too_long", user_message=TOO_LONG_REPLY)

    return InboundMessage(
        external_thread_id=str(chat.get("id", "")),
        external_message_id=str(message.get("message_id", "")),
        external_user_id=str(sender.get("id", "")),
        text=text,
        sent_at=sent_at,
    )

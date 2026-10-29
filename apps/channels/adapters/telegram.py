"""Telegram adapter — the only file that knows what a Telegram update looks like."""
from __future__ import annotations

import logging

from apps.channels.adapters.base import ChannelAdapter, InboundMessage
from apps.channels.telegram.client import (
    TelegramBadRequest,
    TelegramError,
    TelegramRateLimited,
    TelegramUnauthorized,
    TelegramUnavailable,
)
from apps.channels.telegram.updates import parse_update

logger = logging.getLogger("apps.channels.telegram")

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class TelegramAdapter(ChannelAdapter):
    channel_type = "TELEGRAM"

    def client(self, channel):
        from apps.channels.telegram.client import TelegramClient

        return TelegramClient(channel.get_credentials())

    def verify_request(self, channel, request) -> bool:
        presented = request.headers.get(SECRET_HEADER, "")
        return channel.verify_webhook_secret(presented)

    def parse_update(self, channel, payload: dict) -> InboundMessage:
        return parse_update(payload)

    def send_text(self, channel, thread_id: str, parts: list[str], *, reply_to: str | None = None) -> list[str]:
        client = self.client(channel)
        sent: list[str] = []
        for index, part in enumerate(parts):
            # Only the first part replies to the user's message.
            sent.append(client.send_message(thread_id, part, reply_to=reply_to if index == 0 else None))
        return sent

    def register(self, channel, webhook_url: str, secret: str) -> None:
        self.client(channel).set_webhook(webhook_url, secret)

    def deregister(self, channel) -> None:
        try:
            self.client(channel).delete_webhook()
        except (TelegramError, TelegramBadRequest, TelegramUnauthorized, TelegramRateLimited, TelegramUnavailable):
            # Best effort: a revoked token must not block a disconnect.
            logger.warning("channel.telegram.deregister_failed channel=%s", channel.id)

    def describe(self, channel) -> dict:
        bot = self.client(channel).get_me()
        return {"bot_id": bot.id, "bot_username": bot.username}

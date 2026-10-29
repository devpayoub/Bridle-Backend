"""The contract every channel implements.

Adding WhatsApp or Slack should mean writing one adapter module and registering
it — nothing else in the app knows what a Telegram update looks like.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InboundMessage:
    external_thread_id: str
    external_message_id: str
    external_user_id: str
    text: str
    sent_at: datetime | None = None


class IgnoredUpdate(Exception):
    """Raised when an update must not reach the model.

    ``reason`` is stored on the event (an enum-like string, never user data).
    ``user_message``, when set, is sent back to the person who wrote in.
    """

    def __init__(self, reason: str, *, user_message: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.user_message = user_message


class ChannelAdapter(ABC):
    channel_type: str

    @abstractmethod
    def verify_request(self, channel, request) -> bool:
        """True when the inbound request really came from the provider."""

    @abstractmethod
    def parse_update(self, channel, payload: dict) -> InboundMessage:
        """Normalise a provider update, or raise ``IgnoredUpdate``."""

    @abstractmethod
    def send_text(self, channel, thread_id: str, parts: list[str], *, reply_to: str | None = None) -> list[str]:
        """Send the reply parts; return the provider message ids."""

    @abstractmethod
    def register(self, channel, webhook_url: str, secret: str) -> None:
        """Tell the provider where to deliver updates."""

    @abstractmethod
    def deregister(self, channel) -> None:
        """Best-effort: stop delivery. Must not raise."""

    @abstractmethod
    def describe(self, channel) -> dict:
        """Provider-side identity for the console (bot username, etc.)."""

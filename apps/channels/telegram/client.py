"""Minimal Telegram Bot API client.

Two rules hold this file together:

1. **The token is in the URL path** (``/bot<token>/sendMessage``). Every ``httpx``
   exception string, and any log line that formats a URL, can carry it. So: no
   ``logger`` calls here, and every raised error is sanitised with ``from None``
   so the original exception never appears in a traceback.
2. **The base URL is a constant.** No caller can point this client at another
   host, and redirects are not followed — that removes the SSRF surface entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings


class TelegramError(Exception):
    """Base class for sanitised Telegram failures."""

    code = "telegram_error"


class TelegramUnauthorized(TelegramError):
    """401 — the token is revoked or wrong. Never retryable."""

    code = "telegram_unauthorized"


class TelegramBadRequest(TelegramError):
    """400/403 — the payload was rejected (bad chat id, blocked by user, …)."""

    code = "telegram_bad_request"


class TelegramRateLimited(TelegramError):
    """429 — back off for ``retry_after`` seconds."""

    code = "telegram_rate_limited"

    def __init__(self, message: str, retry_after: int = 1):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after or 1))


class TelegramUnavailable(TelegramError):
    """5xx or a transport failure."""

    code = "telegram_unavailable"


@dataclass(frozen=True)
class TelegramBot:
    id: str
    username: str
    first_name: str


class TelegramClient:
    def __init__(self, token: str, *, timeout: float | None = None):
        self._token = (token or "").strip()
        self._timeout = timeout if timeout is not None else settings.CHANNELS_TELEGRAM_TIMEOUT

    # -- transport ---------------------------------------------------------
    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{settings.CHANNELS_TELEGRAM_API_BASE}/bot{self._token}/{method}"
        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(url, json=payload or {})
        except httpx.HTTPError:
            # Deliberately not chained: the cause holds the URL, and the URL holds
            # the token.
            raise TelegramUnavailable("Could not reach Telegram.") from None

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code == 200 and body.get("ok"):
            return body.get("result")

        description = str(body.get("description") or "")[:200]
        if response.status_code == 401:
            raise TelegramUnauthorized("Telegram rejected the bot token.") from None
        if response.status_code == 429:
            params = body.get("parameters") or {}
            raise TelegramRateLimited(
                "Telegram rate limited the request.",
                retry_after=int(params.get("retry_after") or 1),
            ) from None
        if response.status_code >= 500:
            raise TelegramUnavailable("Telegram is unavailable.") from None
        raise TelegramBadRequest(description or "Telegram rejected the request.") from None

    # -- methods -----------------------------------------------------------
    def get_me(self) -> TelegramBot:
        result = self._call("getMe") or {}
        return TelegramBot(
            id=str(result.get("id", "")),
            username=str(result.get("username", "")),
            first_name=str(result.get("first_name", "")),
        )

    def set_webhook(
        self,
        url: str,
        secret_token: str,
        *,
        allowed_updates: list[str] | None = None,
        drop_pending_updates: bool = True,
    ) -> None:
        self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": allowed_updates or ["message"],
                "drop_pending_updates": drop_pending_updates,
            },
        )

    def delete_webhook(self, *, drop_pending_updates: bool = True) -> None:
        self._call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_webhook_info(self) -> dict:
        return self._call("getWebhookInfo") or {}

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            # No parse_mode: model output is untrusted and malformed entities make
            # Telegram reject the whole message.
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to and settings.CHANNELS_TELEGRAM_REPLY_TO_MESSAGE:
            payload["reply_parameters"] = {
                "message_id": int(reply_to),
                "allow_sending_without_reply": True,
            }
        result = self._call("sendMessage", payload) or {}
        return str(result.get("message_id", ""))

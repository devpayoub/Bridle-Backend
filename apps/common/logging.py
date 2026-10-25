"""Structured logging helpers: request correlation and secret redaction."""
from __future__ import annotations

import contextvars
import json
import logging
import re
from typing import Any

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")
_organization_id: contextvars.ContextVar[str] = contextvars.ContextVar("organization_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


def set_log_context(*, user_id: str | None = None, organization_id: str | None = None) -> None:
    """Attach identity information to every log record produced by this request."""
    if user_id is not None:
        _user_id.set(str(user_id))
    if organization_id is not None:
        _organization_id.set(str(organization_id))


class RequestContextFilter(logging.Filter):
    """Inject correlation identifiers into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.user_id = _user_id.get()
        record.organization_id = _organization_id.get()
        return True


_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk_[REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-]{10,})"), r"\1 [REDACTED]"),
    (
        re.compile(r"(?i)((?:api[_-]?key|secret|password|token|authorization)\"?\s*[:=]\s*\"?)([^\s\",}]{4,})"),
        r"\1[REDACTED]",
    ),
)


def redact(value: str) -> str:
    """Remove credential-shaped substrings from a log message."""
    for pattern, replacement in _REDACTION_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class SecretRedactionFilter(logging.Filter):
    """Last line of defence so secrets never reach log sinks."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed format args
            return True
        record.msg = redact(message)
        record.args = ()
        return True


_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "request_id",
    "user_id",
    "organization_id",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter suitable for container log collection."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "organization_id": getattr(record, "organization_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    value = str(value)
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)

"""Retry helper with exponential backoff.

Only *idempotent* operations are retried. ``generate_response`` is not retried by
default because a provider may have already billed the call; the chat endpoint
returns a retryable error instead and lets the client decide (protected by
``Idempotency-Key``).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

from apps.ai_gateway.base import ProviderError

logger = logging.getLogger("apps.ai_gateway.retry")

T = TypeVar("T")

RETRYABLE_CODES = {"provider_timeout", "provider_rate_limited", "provider_unavailable"}


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, ProviderError) and getattr(exc, "code", "") in RETRYABLE_CODES


def with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Execute ``operation`` retrying transient failures with backoff."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last_error = exc
            if not is_retryable(exc) or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += jitter() * 0.25 * delay
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                delay = max(delay, float(retry_after))
            logger.warning(
                "Provider call failed (attempt %s/%s, code=%s); retrying in %.2fs",
                attempt,
                attempts,
                getattr(exc, "code", "unknown"),
                delay,
            )
            sleep(delay)
    raise last_error  # type: ignore[misc]

"""Redis-backed fixed-window rate limiting.

Design notes
------------
* Limits are configured in ``settings.RATE_LIMITS`` as ``scope -> {limit, window}``.
* Counters live in the Django cache (Redis in production, locmem in tests).
* Rate limiting **fails open**: if the cache is unavailable the request is
  allowed and a warning is logged. Availability of the API is prioritised over
  this abuse control. Quota enforcement (see ``apps.usage.quota``) is a
  different control and fails closed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("apps.usage.ratelimit")


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    scope: str


def _bucket_key(scope: str, identifier: str, window: int) -> tuple[str, int]:
    now = int(time.time())
    bucket = now // window
    return f"rl:{scope}:{identifier}:{bucket}", window - (now % window)


def check_rate_limit(scope: str, identifier: str, *, increment: bool = True) -> RateLimitResult:
    """Consume one unit from ``scope`` for ``identifier``.

    ``identifier`` should be a stable, non-secret value (user id, organization id,
    API key id or a hashed client IP).
    """
    config = settings.RATE_LIMITS.get(scope)
    if not config or not getattr(settings, "RATE_LIMIT_ENABLED", True):
        return RateLimitResult(True, 0, 0, 0, scope)

    limit = int(config["limit"])
    window = int(config["window"])
    key, ttl = _bucket_key(scope, identifier, window)

    try:
        if increment:
            try:
                current = cache.incr(key)
            except ValueError:
                # Key absent (or non-integer); create it with the first hit.
                cache.add(key, 1, timeout=ttl)
                current = int(cache.get(key, 1) or 1)
        else:
            current = int(cache.get(key, 0) or 0)
    except Exception:
        logger.warning("Rate limit cache unavailable; failing open", exc_info=True)
        return RateLimitResult(True, limit, limit, 0, scope)

    remaining = max(0, limit - current)
    return RateLimitResult(
        allowed=current <= limit,
        limit=limit,
        remaining=remaining,
        retry_after=max(1, ttl),
        scope=scope,
    )


def reset_rate_limit(scope: str, identifier: str) -> None:
    """Clear the current window (used after a successful login to forgive typos)."""
    config = settings.RATE_LIMITS.get(scope)
    if not config:
        return
    key, _ = _bucket_key(scope, identifier, int(config["window"]))
    try:
        cache.delete(key)
    except Exception:  # pragma: no cover - cache outage
        logger.warning("Unable to reset rate limit counter", exc_info=True)


def client_identifier(request) -> str:
    """Derive a coarse client identifier without storing raw IPs indefinitely.

    Behind a proxy the left-most ``X-Forwarded-For`` entry is used; the value is
    hashed so counters cannot be trivially mapped back to an address in Redis.
    """
    from apps.common.crypto import sha256_hex

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "unknown")
    return sha256_hex(ip)[:32]


def enforce_rate_limit(scope: str, identifier: str) -> RateLimitResult:
    """Check a limit and raise the platform 429 error when it is exhausted."""
    from apps.common.exceptions import RateLimitExceeded

    result = check_rate_limit(scope, identifier)
    if not result.allowed:
        raise RateLimitExceeded(retry_after=result.retry_after, scope=scope)
    return result

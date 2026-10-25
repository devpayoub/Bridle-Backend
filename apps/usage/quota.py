"""Usage quota reservation.

Quotas are enforced **before** an expensive provider call and released if the
call fails, so a failed request does not burn the customer's allowance.

Concurrency: a Redis counter is incremented atomically, which keeps concurrent
requests from all passing the check at once. The counter is a *soft* limit — a
small overshoot is possible if Redis is unavailable and the database fallback is
used (documented limitation).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone

from apps.billing.services import get_entitlements
from apps.common.exceptions import QuotaExceeded

logger = logging.getLogger("apps.usage.quota")

METRIC_CHAT = "chat_requests"
METRIC_POLICY = "policy_evaluations"

_METRIC_TO_ENTITLEMENT = {
    METRIC_CHAT: "max_monthly_chat_requests",
    METRIC_POLICY: "max_monthly_policy_evaluations",
}


@dataclass(frozen=True)
class QuotaState:
    metric: str
    limit: int
    used: int
    remaining: int
    period_end: object


def _period_bounds(organization) -> tuple[object, object]:
    subscription = getattr(organization, "subscription", None)
    if subscription is not None:
        return subscription.current_period_start, subscription.current_period_end
    now = timezone.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timezone.timedelta(days=32)).replace(day=1)
    return start, end


def _cache_key(organization_id, metric: str, period_start) -> str:
    return f"quota:{organization_id}:{metric}:{int(period_start.timestamp())}"


def _db_usage(organization, metric: str, period_start, period_end) -> int:
    from apps.usage.models import UsageRecord

    kind = UsageRecord.Kind.CHAT if metric == METRIC_CHAT else UsageRecord.Kind.POLICY_EVALUATION
    return UsageRecord.objects.filter(
        organization=organization,
        kind=kind,
        created_at__gte=period_start,
        created_at__lt=period_end,
    ).count()


def get_quota_state(organization, metric: str) -> QuotaState:
    entitlements = get_entitlements(organization)
    limit = getattr(entitlements, _METRIC_TO_ENTITLEMENT[metric])
    period_start, period_end = _period_bounds(organization)
    key = _cache_key(organization.id, metric, period_start)

    try:
        used = int(cache.get(key, 0) or 0)
    except Exception:  # pragma: no cover - cache outage
        used = _db_usage(organization, metric, period_start, period_end)

    return QuotaState(
        metric=metric,
        limit=limit,
        used=used,
        remaining=max(0, limit - used),
        period_end=period_end,
    )


@contextmanager
def reserve_quota(organization, metric: str, amount: int = 1):
    """Reserve ``amount`` units, releasing them if the wrapped block raises."""
    if metric not in _METRIC_TO_ENTITLEMENT:
        raise ValueError(f"Unknown quota metric: {metric}")

    entitlements = get_entitlements(organization)
    limit = getattr(entitlements, _METRIC_TO_ENTITLEMENT[metric])
    period_start, period_end = _period_bounds(organization)
    key = _cache_key(organization.id, metric, period_start)
    ttl = max(60, int((period_end - timezone.now()).total_seconds()))
    reserved_via_cache = True

    try:
        cache.add(key, 0, timeout=ttl)
        used = cache.incr(key, amount)
    except Exception:
        logger.warning("Quota cache unavailable; using database fallback", exc_info=True)
        reserved_via_cache = False
        used = _db_usage(organization, metric, period_start, period_end) + amount

    if used > limit:
        if reserved_via_cache:
            try:
                cache.decr(key, amount)
            except Exception:  # pragma: no cover
                pass
        retry_after = max(1, int((period_end - timezone.now()).total_seconds()))
        raise _quota_error(metric, limit, period_end, retry_after)

    try:
        yield QuotaState(metric=metric, limit=limit, used=used, remaining=max(0, limit - used), period_end=period_end)
    except Exception:
        if reserved_via_cache:
            try:
                cache.decr(key, amount)
            except Exception:  # pragma: no cover
                pass
        raise


def _quota_error(metric: str, limit: int, period_end, retry_after: int) -> QuotaExceeded:
    error = QuotaExceeded(
        "Monthly quota exhausted for this metric.",
        extra={"metric": metric, "limit": limit, "period_end": period_end.isoformat()},
    )
    error.retry_after = retry_after
    return error

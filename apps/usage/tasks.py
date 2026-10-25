"""Usage roll-up task feeding the billing layer."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db.models import Count
from django.utils import timezone

from apps.billing.models import UsageSummary
from apps.billing.services import ensure_subscription
from apps.organizations.models import Organization
from apps.usage.models import UsageRecord

logger = logging.getLogger("apps.usage.tasks")

_METRICS = {
    "chat_requests": UsageRecord.Kind.CHAT,
    "policy_evaluations": UsageRecord.Kind.POLICY_EVALUATION,
}


@shared_task(name="usage.roll_up_summaries")
def roll_up_usage_summaries() -> int:
    """Recompute period usage summaries.

    Idempotent (update_or_create on the natural key) and safe to re-run after a
    failure. Summaries are reporting data only; quota enforcement reads live
    counters.
    """
    updated = 0
    organizations = Organization.objects.filter(status=Organization.Status.ACTIVE).select_related(
        "subscription__plan"
    )

    for organization in organizations.iterator():
        subscription = ensure_subscription(organization)
        for metric, kind in _METRICS.items():
            quantity = UsageRecord.objects.filter(
                organization=organization,
                kind=kind,
                created_at__gte=subscription.current_period_start,
                created_at__lt=subscription.current_period_end,
            ).aggregate(total=Count("id"))["total"] or 0
            UsageSummary.objects.update_or_create(
                organization=organization,
                period_start=subscription.current_period_start,
                metric=metric,
                defaults={
                    "period_end": subscription.current_period_end,
                    "quantity": quantity,
                    "updated_at": timezone.now(),
                },
            )
            updated += 1
    return updated

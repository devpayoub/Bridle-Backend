"""Entitlement checks.

Every limit is enforced server side. Clients can read limits but never influence
them: plan and subscription state come from the database only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.defaults import DEFAULT_PLANS
from apps.billing.models import Plan, Subscription, UsageLimit
from apps.common.exceptions import EntitlementExceeded

logger = logging.getLogger("apps.billing")


@dataclass(frozen=True)
class Entitlements:
    plan_code: str
    max_assistants: int
    max_monthly_chat_requests: int
    max_monthly_policy_evaluations: int
    max_knowledge_bytes: int
    max_members: int
    max_api_keys: int
    period_start: object
    period_end: object


def get_default_plan() -> Plan:
    plan = Plan.objects.filter(is_default=True).first()
    if plan is not None:
        return plan
    # Self-healing fallback: seed the free plan if the data migration was skipped.
    spec = next(p for p in DEFAULT_PLANS if p["code"] == "free")
    return Plan.objects.get_or_create(code=spec["code"], defaults=spec)[0]


@transaction.atomic
def ensure_subscription(organization) -> Subscription:
    existing = Subscription.objects.select_related("plan").filter(organization=organization).first()
    if existing is not None:
        return existing
    now = timezone.now()
    return Subscription.objects.create(
        organization=organization,
        plan=get_default_plan(),
        status=Subscription.Status.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )


def _override(organization, metric: str) -> int | None:
    row = UsageLimit.objects.filter(organization=organization, metric=metric).first()
    return row.limit_value if row else None


def get_entitlements(organization) -> Entitlements:
    subscription = getattr(organization, "subscription", None)
    if subscription is None:
        subscription = ensure_subscription(organization)
    plan = subscription.plan

    def resolve(metric: str, fallback: int) -> int:
        override = _override(organization, metric)
        return fallback if override is None else override

    return Entitlements(
        plan_code=plan.code,
        max_assistants=resolve(UsageLimit.Metric.ASSISTANTS, plan.max_assistants),
        max_monthly_chat_requests=resolve(
            UsageLimit.Metric.CHAT_REQUESTS, plan.max_monthly_chat_requests
        ),
        max_monthly_policy_evaluations=resolve(
            UsageLimit.Metric.POLICY_EVALUATIONS, plan.max_monthly_policy_evaluations
        ),
        max_knowledge_bytes=resolve(UsageLimit.Metric.KNOWLEDGE_BYTES, plan.max_knowledge_bytes),
        max_members=resolve(UsageLimit.Metric.MEMBERS, plan.max_members),
        max_api_keys=resolve(UsageLimit.Metric.API_KEYS, plan.max_api_keys),
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
    )


def assert_can_create_assistant(organization, *, current_count: int) -> None:
    limit = get_entitlements(organization).max_assistants
    if current_count >= limit:
        raise EntitlementExceeded(
            f"Your plan allows at most {limit} assistants. Archive an assistant or upgrade your plan.",
            extra={"limit": limit, "metric": "assistants"},
        )


def assert_can_add_member(organization, *, current_count: int) -> None:
    limit = get_entitlements(organization).max_members
    if current_count >= limit:
        raise EntitlementExceeded(
            f"Your plan allows at most {limit} team members.",
            extra={"limit": limit, "metric": "members"},
        )


def assert_can_create_api_key(organization, *, current_count: int) -> None:
    limit = get_entitlements(organization).max_api_keys
    if current_count >= limit:
        raise EntitlementExceeded(
            f"Your plan allows at most {limit} active API keys.",
            extra={"limit": limit, "metric": "api_keys"},
        )


def assert_can_store_knowledge(organization, *, additional_bytes: int) -> None:
    from apps.knowledge.models import Document

    limit = get_entitlements(organization).max_knowledge_bytes
    used = (
        Document.objects.filter(organization=organization, deleted_at__isnull=True).aggregate(
            total=Sum("size_bytes")
        )["total"]
        or 0
    )
    if used + additional_bytes > limit:
        raise EntitlementExceeded(
            "Knowledge storage limit reached for your plan.",
            extra={"limit_bytes": limit, "used_bytes": used, "requested_bytes": additional_bytes},
        )


def knowledge_usage_bytes(organization) -> int:
    from apps.knowledge.models import Document

    return (
        Document.objects.filter(organization=organization, deleted_at__isnull=True).aggregate(
            total=Sum("size_bytes")
        )["total"]
        or 0
    )

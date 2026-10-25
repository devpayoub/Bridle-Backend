"""Billing domain models.

The platform deliberately keeps billing *internal* and provider agnostic:

* ``Plan`` describes entitlements (limits), not prices charged by a gateway.
* ``Subscription`` links an organization to a plan and tracks period state.
* ``UsageSummary`` / ``BillingPeriod`` hold aggregated numbers that a future
  invoicing integration can consume.

No payment provider is wired up in this build; see ``apps.billing.providers`` for
the documented integration seam. Card data is never stored.
"""
from __future__ import annotations

from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Plan(UUIDModel, TimeStampedModel):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price_monthly_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")

    max_assistants = models.PositiveIntegerField(default=1)
    max_monthly_chat_requests = models.PositiveIntegerField(default=1000)
    max_monthly_policy_evaluations = models.PositiveIntegerField(default=1000)
    max_knowledge_bytes = models.BigIntegerField(default=50 * 1024 * 1024)
    max_members = models.PositiveIntegerField(default=3)
    max_api_keys = models.PositiveIntegerField(default=5)

    is_public = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "billing_plan"
        ordering = ("price_monthly_cents", "code")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="uniq_default_plan",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Subscription(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        TRIALING = "TRIALING", "Trialing"
        ACTIVE = "ACTIVE", "Active"
        PAST_DUE = "PAST_DUE", "Past due"
        CANCELED = "CANCELED", "Canceled"

    organization = models.OneToOneField(
        "organizations.Organization", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    cancel_at_period_end = models.BooleanField(default=False)

    # Integration seam: identifiers from an external payment provider.
    external_customer_id = models.CharField(max_length=128, blank=True)
    external_subscription_id = models.CharField(max_length=128, blank=True)

    class Meta:
        db_table = "billing_subscription"
        indexes = [models.Index(fields=["status", "current_period_end"], name="sub_status_period_idx")]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.plan.code}:{self.status}"

    @property
    def is_entitled(self) -> bool:
        return self.status in {self.Status.TRIALING, self.Status.ACTIVE}


class UsageLimit(UUIDModel, TimeStampedModel):
    """Per-organization override of a plan limit (sales-negotiated caps)."""

    class Metric(models.TextChoices):
        CHAT_REQUESTS = "chat_requests", "Chat requests"
        POLICY_EVALUATIONS = "policy_evaluations", "Policy evaluations"
        ASSISTANTS = "assistants", "Assistants"
        MEMBERS = "members", "Members"
        API_KEYS = "api_keys", "API keys"
        KNOWLEDGE_BYTES = "knowledge_bytes", "Knowledge bytes"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="usage_limits"
    )
    metric = models.CharField(max_length=32, choices=Metric.choices)
    limit_value = models.BigIntegerField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "billing_usage_limit"
        constraints = [
            models.UniqueConstraint(fields=["organization", "metric"], name="uniq_org_metric_limit")
        ]


class BillingPeriod(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        INVOICED = "INVOICED", "Invoiced"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="billing_periods"
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "billing_period"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "period_start"], name="uniq_org_billing_period"
            )
        ]
        indexes = [models.Index(fields=["organization", "status"], name="billing_period_org_idx")]


class UsageSummary(UUIDModel, TimeStampedModel):
    """Aggregated usage per organization/period/metric.

    Values are *measured*, not billed: a future invoicing job reconciles them
    against the payment provider before charging.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="usage_summaries"
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    metric = models.CharField(max_length=32)
    quantity = models.BigIntegerField(default=0)

    class Meta:
        db_table = "billing_usage_summary"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "period_start", "metric"], name="uniq_usage_summary"
            )
        ]
        indexes = [models.Index(fields=["organization", "metric"], name="usage_summary_org_idx")]

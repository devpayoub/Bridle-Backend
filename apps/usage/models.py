"""Per-request usage records.

Token counts and costs are only marked as exact when the provider returns
authoritative usage metadata. Otherwise ``token_usage_estimated`` is true and
costs are best-effort estimates that must not be used for invoicing.
"""
from __future__ import annotations

from django.db import models

from apps.common.models import UUIDModel


class UsageRecord(UUIDModel):
    class Kind(models.TextChoices):
        CHAT = "CHAT", "Chat completion"
        POLICY_EVALUATION = "POLICY_EVALUATION", "Policy evaluation"
        EMBEDDING = "EMBEDDING", "Embedding"

    class Outcome(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        REFUSED = "REFUSED", "Refused by policy"
        CLARIFICATION = "CLARIFICATION", "Clarification requested"
        ESCALATED = "ESCALATED", "Escalated"
        ERROR = "ERROR", "Error"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="usage_records"
    )
    assistant = models.ForeignKey(
        "assistants.Assistant", on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_records"
    )
    api_key = models.ForeignKey(
        "api_keys.APIKey", on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_records"
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.CHAT, db_index=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.SUCCESS)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    token_usage_estimated = models.BooleanField(default=True)
    estimated_cost_micros = models.BigIntegerField(null=True, blank=True)

    provider = models.CharField(max_length=32, blank=True)
    model = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    provider_latency_ms = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "usage_record"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="usage_org_created_idx"),
            models.Index(fields=["assistant", "-created_at"], name="usage_assistant_idx"),
            models.Index(fields=["api_key", "-created_at"], name="usage_apikey_idx"),
            models.Index(fields=["organization", "kind", "created_at"], name="usage_org_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.outcome}:{self.organization_id}"

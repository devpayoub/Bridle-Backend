"""Policy configuration and evaluation history models."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class AssistantPolicy(UUIDModel, TimeStampedModel):
    """Enforcement configuration for an assistant.

    Topics live on ``Assistant`` (they are part of the assistant's declared
    domain) while this model holds the *rules* that are applied to them. The
    policy service writes both in one transaction so the two never diverge.

    Platform-mandatory controls (prompt-injection detection, prohibited-content
    screening) are applied by the engine regardless of anything stored here:
    a customer cannot switch them off.
    """

    class UncertaintyHandling(models.TextChoices):
        CLARIFY = "CLARIFY", "Ask a clarifying question"
        REFUSE = "REFUSE", "Refuse politely"

    class EvaluatorMode(models.TextChoices):
        RULES = "RULES", "Deterministic rules only"
        HYBRID = "HYBRID", "Rules with model assistance"

    assistant = models.OneToOneField(
        "assistants.Assistant", on_delete=models.CASCADE, related_name="policy"
    )
    industry = models.CharField(max_length=32, blank=True)
    response_constraints = models.JSONField(default=dict, blank=True)
    escalation_rules = models.JSONField(default=dict, blank=True)
    tool_permissions = models.JSONField(default=dict, blank=True)
    uncertainty_handling = models.CharField(
        max_length=16, choices=UncertaintyHandling.choices, default=UncertaintyHandling.CLARIFY
    )
    evaluator_mode = models.CharField(
        max_length=16, choices=EvaluatorMode.choices, default=EvaluatorMode.RULES
    )
    allow_threshold = models.FloatField(default=0.60)
    clarify_threshold = models.FloatField(default=0.34)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "policies_assistant_policy"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(allow_threshold__gte=0.0) & models.Q(allow_threshold__lte=1.0),
                name="policy_allow_threshold_range",
            ),
            models.CheckConstraint(
                condition=models.Q(clarify_threshold__gte=0.0) & models.Q(clarify_threshold__lte=1.0),
                name="policy_clarify_threshold_range",
            ),
            models.CheckConstraint(
                condition=models.Q(clarify_threshold__lte=models.F("allow_threshold")),
                name="policy_threshold_order",
            ),
        ]

    def __str__(self) -> str:
        return f"policy({self.assistant_id}) v{self.version}"


class PolicyEvaluation(UUIDModel):
    """Audit record of a policy decision.

    ``metadata`` contains structured evidence (matched topics, scores, thresholds)
    for authorised audit access. It never contains hidden chain-of-thought.
    """

    class Phase(models.TextChoices):
        REQUEST = "REQUEST", "Request"
        RESPONSE = "RESPONSE", "Response"

    class Decision(models.TextChoices):
        ALLOW = "ALLOW", "Allow"
        REFUSE = "REFUSE", "Refuse"
        CLARIFY = "CLARIFY", "Clarify"
        ESCALATE = "ESCALATE", "Escalate"
        REVIEW = "REVIEW", "Review"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="policy_evaluations"
    )
    assistant = models.ForeignKey(
        "assistants.Assistant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policy_evaluations",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policy_evaluations",
    )
    message = models.ForeignKey(
        "conversations.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policy_evaluations",
    )
    phase = models.CharField(max_length=16, choices=Phase.choices)
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason_code = models.CharField(max_length=48, db_index=True)
    confidence = models.FloatField(null=True, blank=True)
    evaluator = models.CharField(max_length=32)
    evaluator_version = models.CharField(max_length=32)
    policy_version = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "policies_evaluation"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="policyeval_org_idx"),
            models.Index(fields=["assistant", "-created_at"], name="policyeval_assistant_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.phase}:{self.decision}:{self.reason_code}"

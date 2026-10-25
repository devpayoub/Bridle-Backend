"""Assistant models and immutable configuration snapshots."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Assistant(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        ARCHIVED = "ARCHIVED", "Archived"

    class DomainType(models.TextChoices):
        HOTEL = "HOTEL", "Hotel"
        HOSPITAL = "HOSPITAL", "Hospital"
        ECOMMERCE = "ECOMMERCE", "E-commerce"
        BOOKING = "BOOKING", "Booking platform"
        UNIVERSITY = "UNIVERSITY", "University"
        GENERIC = "GENERIC", "Generic business"
        OTHER = "OTHER", "Other"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="assistants"
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    domain_type = models.CharField(max_length=32, choices=DomainType.choices, default=DomainType.GENERIC)

    allowed_topics = models.JSONField(default=list, blank=True)
    prohibited_topics = models.JSONField(default=list, blank=True)
    system_instructions = models.TextField(blank=True)
    fallback_message = models.CharField(max_length=500, default="I can only help with questions about our services.")
    default_language = models.CharField(max_length=16, default="en")

    model_provider = models.CharField(max_length=32, default="openai")
    model_name = models.CharField(max_length=100, default="gpt-4o-mini")
    generation_parameters = models.JSONField(default=dict, blank=True)
    maximum_output_tokens = models.PositiveIntegerField(default=600)

    knowledge_required = models.BooleanField(
        default=False, help_text="When true, answers must be grounded in retrieved knowledge."
    )
    knowledge_bases = models.ManyToManyField(
        "knowledge.KnowledgeBase", related_name="assistants", blank=True
    )

    config_version = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_assistants"
    )

    class Meta:
        db_table = "assistants_assistant"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="uniq_assistant_slug_per_org"),
            models.CheckConstraint(
                condition=models.Q(status__in=["DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"]),
                name="assistant_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(maximum_output_tokens__gte=1) & models.Q(maximum_output_tokens__lte=4096),
                name="assistant_output_tokens_range",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="assistant_org_status_idx"),
            models.Index(fields=["organization", "-created_at"], name="assistant_org_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"

    @property
    def accepts_requests(self) -> bool:
        return self.status == self.Status.ACTIVE


class AssistantConfigurationVersion(UUIDModel):
    """Immutable snapshot of an assistant configuration.

    Every mutation writes a new row, so a conversation can always be traced back
    to the exact configuration that produced it (``Conversation.config_version``).
    """

    assistant = models.ForeignKey(
        Assistant, on_delete=models.CASCADE, related_name="configuration_versions"
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    change_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "assistants_configuration_version"
        ordering = ("-version",)
        constraints = [
            models.UniqueConstraint(fields=["assistant", "version"], name="uniq_assistant_config_version")
        ]
        indexes = [models.Index(fields=["assistant", "-version"], name="config_version_idx")]

    def __str__(self) -> str:
        return f"{self.assistant_id} v{self.version}"

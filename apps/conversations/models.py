"""Conversation, message and idempotency models.

Messages are stored relationally (never as one JSON blob) so retention,
anonymisation and pagination stay cheap and auditable.
"""
from __future__ import annotations

from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Conversation(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"
        ANONYMIZED = "ANONYMIZED", "Anonymised"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="conversations"
    )
    assistant = models.ForeignKey(
        "assistants.Assistant", on_delete=models.CASCADE, related_name="conversations"
    )
    external_user_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="Opaque identifier supplied by the customer's own application.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    config_version = models.PositiveIntegerField(
        default=0, help_text="Assistant configuration version used for this conversation."
    )
    metadata = models.JSONField(default=dict, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    retention_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "conversations_conversation"
        ordering = ("-last_message_at", "-created_at")
        indexes = [
            models.Index(fields=["organization", "-last_message_at"], name="conv_org_last_msg_idx"),
            models.Index(fields=["assistant", "-last_message_at"], name="conv_assistant_idx"),
            models.Index(fields=["organization", "status"], name="conv_org_status_idx"),
        ]

    def __str__(self) -> str:
        return f"conversation({self.id})"


class Message(UUIDModel):
    class Role(models.TextChoices):
        USER = "USER", "User"
        ASSISTANT = "ASSISTANT", "Assistant"
        SYSTEM = "SYSTEM", "System"
        TOOL = "TOOL", "Tool"

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        BLOCKED = "BLOCKED", "Blocked by policy"
        FAILED = "FAILED", "Failed"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COMPLETED)

    policy_decision = models.CharField(max_length=16, blank=True)
    refusal_reason_code = models.CharField(max_length=48, blank=True)
    citations = models.JSONField(default=list, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "conversations_message"
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="msg_conv_created_idx"),
            models.Index(fields=["conversation", "role"], name="msg_conv_role_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=["USER", "ASSISTANT", "SYSTEM", "TOOL"]),
                name="message_role_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.role}:{self.conversation_id}"


class IdempotencyRecord(UUIDModel):
    """Replay protection for chat completions.

    Stores a fingerprint of the request body and the serialised response so a
    retried request returns the original result instead of double-charging the
    customer or duplicating messages.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="idempotency_records"
    )
    key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveIntegerField(default=200)
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "conversations_idempotency"
        constraints = [models.UniqueConstraint(fields=["organization", "key"], name="uniq_org_idempotency")]
        indexes = [models.Index(fields=["expires_at"], name="idempotency_expiry_idx")]

"""Append-only audit trail for security-relevant events.

Audit rows are never updated or deleted through the API. Retention is handled by
a scheduled purge task (``AUDIT_LOG_RETENTION_DAYS``) so the table does not grow
without bound. ``metadata`` must never contain secrets or full prompts.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import UUIDModel


class AuditLog(UUIDModel):
    class Action(models.TextChoices):
        ORGANIZATION_CREATED = "organization.created", "Organization created"
        ORGANIZATION_UPDATED = "organization.updated", "Organization updated"
        MEMBER_ADDED = "member.added", "Member added"
        MEMBER_ROLE_CHANGED = "member.role_changed", "Member role changed"
        MEMBER_REMOVED = "member.removed", "Member removed"
        API_KEY_CREATED = "api_key.created", "API key created"
        API_KEY_REVOKED = "api_key.revoked", "API key revoked"
        API_KEY_ROTATED = "api_key.rotated", "API key rotated"
        API_KEY_DELETED = "api_key.deleted", "API key deleted"
        ASSISTANT_CREATED = "assistant.created", "Assistant created"
        ASSISTANT_UPDATED = "assistant.updated", "Assistant updated"
        ASSISTANT_STATUS_CHANGED = "assistant.status_changed", "Assistant status changed"
        ASSISTANT_DUPLICATED = "assistant.duplicated", "Assistant duplicated"
        POLICY_UPDATED = "policy.updated", "Policy updated"
        KNOWLEDGE_CREATED = "knowledge.created", "Knowledge base created"
        KNOWLEDGE_UPDATED = "knowledge.updated", "Knowledge base updated"
        DOCUMENT_UPLOADED = "document.uploaded", "Document uploaded"
        DOCUMENT_DELETED = "document.deleted", "Document deleted"
        DOCUMENT_REPROCESSED = "document.reprocessed", "Document reprocessed"
        CONVERSATION_DELETED = "conversation.deleted", "Conversation deleted"
        ACCOUNT_DEACTIVATED = "account.deactivated", "Account deactivated"
        ADMIN_ACCESS = "admin.access", "Administrative access"
        AI_SETTINGS_UPDATED = "ai_settings.updated", "AI provider settings updated"
        AI_SETTINGS_CLEARED = "ai_settings.cleared", "AI provider settings cleared"
        PLATFORM_ORGANIZATION_STATUS = "platform.organization_status", "Workspace status changed by platform admin"
        PLATFORM_USER_UPDATED = "platform.user_updated", "Account changed by platform admin"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, null=True, blank=True, related_name="audit_logs"
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    api_key = models.ForeignKey(
        "api_keys.APIKey", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=64, choices=Action.choices, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="audit_org_created_idx"),
            models.Index(fields=["organization", "action"], name="audit_org_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"

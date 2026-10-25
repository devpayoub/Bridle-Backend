"""Admin for the audit trail.

The audit log is append-only by design, so every write path is disabled here.
"""
from __future__ import annotations

from django.contrib import admin

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "organization", "actor_email", "target_type", "target_id")
    list_filter = ("action", "target_type")
    search_fields = ("actor_user__email", "target_id", "request_id", "organization__name")
    list_select_related = ("organization", "actor_user", "api_key")
    date_hierarchy = "created_at"
    readonly_fields = (
        "organization",
        "actor_user",
        "api_key",
        "action",
        "target_type",
        "target_id",
        "metadata",
        "request_id",
        "ip_hash",
        "created_at",
    )

    @admin.display(description="Actor")
    def actor_email(self, obj: AuditLog) -> str:
        if obj.actor_user_id:
            return obj.actor_user.email
        if obj.api_key_id:
            return f"{obj.api_key.prefix}…"
        return "system"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

"""Admin for per-request usage records (read-only)."""
from __future__ import annotations

from django.contrib import admin

from apps.usage.models import UsageRecord


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "organization",
        "assistant",
        "kind",
        "outcome",
        "input_tokens",
        "output_tokens",
        "latency_ms",
    )
    list_filter = ("kind", "outcome", "provider")
    search_fields = ("request_id", "organization__name", "assistant__name")
    list_select_related = ("organization", "assistant", "api_key", "conversation")
    date_hierarchy = "created_at"
    readonly_fields = (
        "organization",
        "assistant",
        "api_key",
        "conversation",
        "kind",
        "outcome",
        "request_id",
        "input_tokens",
        "output_tokens",
        "token_usage_estimated",
        "estimated_cost_micros",
        "provider",
        "model",
        "latency_ms",
        "provider_latency_ms",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

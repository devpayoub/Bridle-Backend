"""Admin for assistant policies and recorded policy decisions."""
from __future__ import annotations

from django.contrib import admin

from apps.policies.models import AssistantPolicy, PolicyEvaluation


@admin.register(AssistantPolicy)
class AssistantPolicyAdmin(admin.ModelAdmin):
    list_display = ("assistant", "industry", "uncertainty_handling", "evaluator_mode", "version", "updated_at")
    list_filter = ("uncertainty_handling", "evaluator_mode", "industry")
    search_fields = ("assistant__name", "industry")
    list_select_related = ("assistant", "updated_by")
    autocomplete_fields = ("assistant", "updated_by")
    readonly_fields = ("version", "created_at", "updated_at")


@admin.register(PolicyEvaluation)
class PolicyEvaluationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "phase", "decision", "reason_code", "assistant", "evaluator", "latency_ms")
    list_filter = ("phase", "decision", "evaluator")
    search_fields = ("reason_code", "assistant__name")
    list_select_related = ("organization", "assistant", "conversation", "message")
    date_hierarchy = "created_at"
    readonly_fields = (
        "organization",
        "assistant",
        "conversation",
        "message",
        "phase",
        "decision",
        "reason_code",
        "confidence",
        "evaluator",
        "evaluator_version",
        "policy_version",
        "metadata",
        "latency_ms",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

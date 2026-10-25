"""Admin for assistant configuration and its version history."""
from __future__ import annotations

from django.contrib import admin

from apps.assistants.models import Assistant, AssistantConfigurationVersion


@admin.register(Assistant)
class AssistantAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "status", "domain_type", "model_provider", "updated_at")
    list_filter = ("status", "domain_type", "model_provider", "knowledge_required")
    search_fields = ("name", "slug", "organization__name")
    list_select_related = ("organization", "created_by")
    autocomplete_fields = ("organization", "created_by")
    filter_horizontal = ("knowledge_bases",)
    readonly_fields = ("config_version", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("organization", "name", "slug", "description", "status", "domain_type")}),
        ("Scope", {"fields": ("allowed_topics", "prohibited_topics", "system_instructions", "fallback_message")}),
        ("Runtime", {"fields": ("model_provider", "model_name", "generation_parameters", "maximum_output_tokens")}),
        ("Knowledge", {"fields": ("knowledge_required", "knowledge_bases")}),
        ("Audit", {"fields": ("created_by", "config_version", "created_at", "updated_at")}),
    )


@admin.register(AssistantConfigurationVersion)
class AssistantConfigurationVersionAdmin(admin.ModelAdmin):
    list_display = ("assistant", "version", "change_reason", "created_by", "created_at")
    search_fields = ("assistant__name", "change_reason")
    list_select_related = ("assistant", "created_by")
    readonly_fields = ("assistant", "version", "snapshot", "change_reason", "created_by", "created_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

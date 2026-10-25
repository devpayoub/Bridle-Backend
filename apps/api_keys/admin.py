"""Admin for API keys.

The hashed secret is deliberately excluded: it must never be rendered in a form,
and the plaintext key is only ever returned once by the API.
"""
from __future__ import annotations

from django.contrib import admin

from apps.api_keys.models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "organization", "environment", "is_active", "last_used_at", "created_at")
    list_filter = ("environment", "revoked_at", "expires_at")
    search_fields = ("name", "prefix", "organization__name")
    list_select_related = ("organization", "assistant", "created_by", "rotated_from")
    autocomplete_fields = ("organization", "assistant", "created_by")
    date_hierarchy = "created_at"
    exclude = ("key_hash",)
    readonly_fields = ("prefix", "rotated_from", "last_used_at", "revoked_at", "created_at", "updated_at")

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj: APIKey) -> bool:
        return obj.revoked_at is None

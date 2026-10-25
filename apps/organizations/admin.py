"""Admin for tenants and their memberships."""
from __future__ import annotations

from django.contrib import admin

from apps.organizations.models import Organization, OrganizationMembership


class MembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ("user",)
    fields = ("user", "role", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "timezone", "member_count", "created_at")
    list_filter = ("status", "timezone")
    search_fields = ("name", "slug")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    inlines = (MembershipInline,)

    @admin.display(description="Members")
    def member_count(self, obj: Organization) -> int:
        return obj.memberships.count()


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__email", "user__full_name", "organization__name")
    list_select_related = ("user", "organization", "invited_by")
    autocomplete_fields = ("user", "organization", "invited_by")
    readonly_fields = ("created_at", "updated_at")

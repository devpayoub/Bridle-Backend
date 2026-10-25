"""Admin for plans, subscriptions and quota overrides.

This is the operator surface for the manual entitlement overrides the API does
not expose: adjust a plan, grant a custom usage limit, or open a billing period.
"""
from __future__ import annotations

from django.contrib import admin

from apps.billing.models import BillingPeriod, Plan, Subscription, UsageLimit, UsageSummary


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "price_monthly_cents",
        "currency",
        "max_assistants",
        "max_monthly_chat_requests",
        "is_public",
        "is_default",
    )
    list_filter = ("is_public", "is_default", "currency")
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "current_period_start", "current_period_end")
    list_filter = ("status", "plan")
    search_fields = ("organization__name", "external_customer_id", "external_subscription_id")
    list_select_related = ("organization", "plan")
    autocomplete_fields = ("organization", "plan")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageLimit)
class UsageLimitAdmin(admin.ModelAdmin):
    list_display = ("organization", "metric", "limit_value", "note", "updated_at")
    list_filter = ("metric",)
    search_fields = ("organization__name", "note")
    list_select_related = ("organization",)
    autocomplete_fields = ("organization",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(BillingPeriod)
class BillingPeriodAdmin(admin.ModelAdmin):
    list_display = ("organization", "period_start", "period_end", "status")
    list_filter = ("status",)
    search_fields = ("organization__name",)
    list_select_related = ("organization",)
    autocomplete_fields = ("organization",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageSummary)
class UsageSummaryAdmin(admin.ModelAdmin):
    list_display = ("organization", "metric", "quantity", "period_start", "period_end")
    list_filter = ("metric",)
    search_fields = ("organization__name",)
    list_select_related = ("organization",)
    date_hierarchy = "period_start"
    readonly_fields = ("created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False

"""Django admin for channels.

Read-only by design: connecting or disconnecting a bot changes live behaviour for
a client's customers, so it belongs in the console where it is capability-gated
and audited — not in a free-form admin form. The token is never rendered.
"""
from __future__ import annotations

from django.contrib import admin

from apps.channels.models import Channel, ChannelEvent, ChannelIdentity, ChannelThread


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "organization", "assistant", "status", "external_username", "created_at")
    list_filter = ("type", "status", "created_at")
    search_fields = ("name", "external_username", "external_id", "organization__name")
    readonly_fields = (
        "id",
        "organization",
        "assistant",
        "type",
        "name",
        "status",
        "external_id",
        "external_username",
        "credentials_last4",
        "webhook_registered_at",
        "webhook_error",
        "last_event_at",
        "disabled_at",
        "created_by",
        "created_at",
        "updated_at",
    )
    exclude = ("credentials_ciphertext", "webhook_key_ciphertext", "webhook_secret_digest")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ChannelEvent)
class ChannelEventAdmin(admin.ModelAdmin):
    list_display = ("update_id", "channel", "kind", "status", "attempts", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("update_id", "channel__name")
    readonly_fields = [f.name for f in ChannelEvent._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ChannelThread)
class ChannelThreadAdmin(admin.ModelAdmin):
    list_display = ("external_thread_id", "channel", "status", "conversation", "last_message_at")
    list_filter = ("status",)
    readonly_fields = [f.name for f in ChannelThread._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(ChannelIdentity)
class ChannelIdentityAdmin(admin.ModelAdmin):
    list_display = ("external_user_id", "channel", "is_blocked", "last_seen_at")
    list_filter = ("is_blocked",)
    # `is_blocked` is the one field an operator may flip: it is the abuse kill
    # switch for a single external user.
    readonly_fields = ("id", "organization", "channel", "external_user_id", "first_seen_at", "last_seen_at")

    def has_add_permission(self, request) -> bool:
        return False

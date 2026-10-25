"""Admin for conversation transcripts and idempotency records."""
from __future__ import annotations

from django.contrib import admin

from apps.conversations.models import Conversation, IdempotencyRecord, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "status", "policy_decision", "content", "created_at")
    readonly_fields = fields
    can_delete = False
    max_num = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "assistant", "external_user_id", "status", "message_count", "last_message_at")
    list_filter = ("status",)
    search_fields = ("assistant__name", "external_user_id", "id")
    list_select_related = ("organization", "assistant")
    autocomplete_fields = ("organization", "assistant")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "last_message_at")
    inlines = (MessageInline,)

    @admin.display(description="Messages")
    def message_count(self, obj: Conversation) -> int:
        return obj.messages.count()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "conversation", "role", "status", "policy_decision", "latency_ms")
    list_filter = ("role", "status", "policy_decision")
    search_fields = ("conversation__assistant__name", "refusal_reason_code")
    list_select_related = ("conversation", "conversation__assistant")
    date_hierarchy = "created_at"
    readonly_fields = (
        "conversation",
        "role",
        "content",
        "status",
        "policy_decision",
        "refusal_reason_code",
        "citations",
        "provider_metadata",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ("key", "organization", "response_status", "created_at", "expires_at")
    search_fields = ("key", "organization__name")
    list_select_related = ("organization",)
    readonly_fields = (
        "organization",
        "key",
        "request_fingerprint",
        "response_status",
        "response_body",
        "created_at",
        "expires_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

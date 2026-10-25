"""Conversation and chat serializers."""
from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from apps.conversations.models import Conversation, Message


class ChatRequestSerializer(serializers.Serializer):
    assistant_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
    message = serializers.CharField(max_length=settings.CHAT_MAX_MESSAGE_CHARS, trim_whitespace=True)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_message(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("The message cannot be empty.")
        return cleaned

    def validate_metadata(self, value) -> dict:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("metadata must be an object.")
        if len(value) > 20:
            raise serializers.ValidationError("metadata supports at most 20 keys.")
        clean: dict = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64:
                raise serializers.ValidationError("metadata keys must be short strings.")
            if isinstance(item, (str, int, float, bool)) or item is None:
                clean[key] = item if not isinstance(item, str) else item[:500]
            else:
                raise serializers.ValidationError("metadata values must be scalars.")
        return clean


class ChatResponseSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    message_id = serializers.UUIDField()
    assistant_id = serializers.UUIDField()
    decision = serializers.CharField()
    reason_code = serializers.CharField()
    content = serializers.CharField()
    citations = serializers.ListField(child=serializers.DictField())
    usage = serializers.DictField()
    policy = serializers.DictField()
    latency_ms = serializers.IntegerField()
    request_id = serializers.CharField()


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = (
            "id",
            "role",
            "content",
            "status",
            "policy_decision",
            "refusal_reason_code",
            "citations",
            "input_tokens",
            "output_tokens",
            "latency_ms",
            "created_at",
        )
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    assistant_name = serializers.CharField(source="assistant.name", read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "assistant_id",
            "assistant_name",
            "external_user_id",
            "status",
            "config_version",
            "message_count",
            "last_message_at",
            "created_at",
        )
        read_only_fields = fields

    def get_message_count(self, obj) -> int:
        annotated = getattr(obj, "message_count", None)
        return annotated if annotated is not None else obj.messages.count()

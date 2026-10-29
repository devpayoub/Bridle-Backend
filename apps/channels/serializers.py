"""Channel serializers.

The token is **write-only and never read back**. There is no field on the read
serializer that can carry it, and ``webhook_url`` is only populated for callers
with ``channel:write`` — it is a capability locator, not a public fact.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.channels.models import Channel
from apps.channels.services import webhook_url_for


class ChannelSerializer(serializers.ModelSerializer):
    assistant_name = serializers.CharField(source="assistant.name", read_only=True)
    bot_id = serializers.CharField(source="external_id", read_only=True)
    bot_username = serializers.CharField(source="external_username", read_only=True)
    has_credentials = serializers.BooleanField(read_only=True)
    token_last4 = serializers.CharField(source="credentials_last4", read_only=True)
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = Channel
        fields = (
            "id",
            "type",
            "name",
            "status",
            "assistant_id",
            "assistant_name",
            "bot_id",
            "bot_username",
            "has_credentials",
            "token_last4",
            "webhook_url",
            "webhook_registered_at",
            "webhook_error",
            "last_event_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_webhook_url(self, obj) -> str | None:
        if not self.context.get("include_webhook_url"):
            return None
        return webhook_url_for(obj) or None


class ChannelConnectSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Channel.Type.choices, default=Channel.Type.TELEGRAM)
    assistant_id = serializers.UUIDField()
    bot_token = serializers.CharField(write_only=True, max_length=200, trim_whitespace=True)
    name = serializers.CharField(required=False, allow_blank=True, max_length=120, default="")


class ChannelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=False, max_length=120)
    assistant_id = serializers.UUIDField(required=False)


class ChannelTestResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    bot_id = serializers.CharField(required=False, allow_blank=True)
    bot_username = serializers.CharField(required=False, allow_blank=True)
    latency_ms = serializers.IntegerField(required=False)
    error_code = serializers.CharField(required=False, allow_blank=True)
    error = serializers.CharField(required=False, allow_blank=True)

"""API key serializers. The plaintext secret is only ever present in the creation response."""
from __future__ import annotations

from rest_framework import serializers

from apps.api_keys.models import APIKey
from apps.api_keys.services import SCOPE_CHOICES


class APIKeySerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    assistant_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = APIKey
        fields = (
            "id",
            "name",
            "prefix",
            "environment",
            "scopes",
            "assistant_id",
            "expires_at",
            "revoked_at",
            "last_used_at",
            "is_active",
            "created_at",
        )
        read_only_fields = fields


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    environment = serializers.ChoiceField(choices=APIKey.Environment.choices, default=APIKey.Environment.DEVELOPMENT)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=SCOPE_CHOICES),
        required=False,
        allow_empty=False,
        max_length=20,
    )
    assistant_id = serializers.UUIDField(required=False, allow_null=True)
    expires_in_days = serializers.IntegerField(required=False, min_value=1, max_value=3650, allow_null=True)


class APIKeyCreatedSerializer(APIKeySerializer):
    """Creation response: the only place the plaintext secret ever appears."""

    key = serializers.CharField(read_only=True)

    class Meta(APIKeySerializer.Meta):
        fields = APIKeySerializer.Meta.fields + ("key",)
        read_only_fields = fields

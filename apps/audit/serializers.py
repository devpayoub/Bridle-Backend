"""Audit log serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor_user.email", read_only=True, default=None)
    api_key_prefix = serializers.CharField(source="api_key.prefix", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "actor_email",
            "api_key_prefix",
            "target_type",
            "target_id",
            "metadata",
            "request_id",
            "created_at",
        )
        read_only_fields = fields

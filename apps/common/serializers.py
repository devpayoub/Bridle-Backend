"""Small serializers for operational and simple response payloads."""
from __future__ import annotations

from rest_framework import serializers


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class MessageSerializer(serializers.Serializer):
    detail = serializers.CharField()
    user = serializers.DictField(required=False)


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()


class ReadySerializer(serializers.Serializer):
    status = serializers.CharField()
    checks = serializers.DictField(child=serializers.CharField())
    vector_search = serializers.CharField()

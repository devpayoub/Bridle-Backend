"""Knowledge API serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.knowledge.models import Document, KnowledgeBase


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBase
        fields = ("id", "name", "slug", "description", "document_count", "created_at", "updated_at")
        read_only_fields = fields

    def get_document_count(self, obj) -> int:
        annotated = getattr(obj, "document_count", None)
        if annotated is not None:
            return annotated
        return obj.documents.filter(deleted_at__isnull=True).count()


class KnowledgeBaseCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class DocumentSerializer(serializers.ModelSerializer):
    knowledge_base_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Document
        fields = (
            "id",
            "knowledge_base_id",
            "title",
            "original_filename",
            "content_type",
            "size_bytes",
            "status",
            "chunk_count",
            "page_count",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class DocumentReprocessSerializer(serializers.Serializer):
    pass

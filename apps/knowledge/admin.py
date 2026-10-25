"""Admin for knowledge bases, documents and ingestion jobs."""
from __future__ import annotations

from django.contrib import admin

from apps.knowledge.models import (
    Document,
    DocumentChunk,
    DocumentProcessingJob,
    KnowledgeBase,
    RetrievalRecord,
)


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "document_count", "created_at")
    search_fields = ("name", "slug", "organization__name")
    list_select_related = ("organization", "created_by")
    autocomplete_fields = ("organization", "created_by")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Documents")
    def document_count(self, obj: KnowledgeBase) -> int:
        return obj.documents.filter(deleted_at__isnull=True).count()


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "knowledge_base", "status", "chunk_count", "size_bytes", "created_at")
    list_filter = ("status", "content_type")
    search_fields = ("title", "original_filename", "knowledge_base__name")
    list_select_related = ("organization", "knowledge_base", "uploaded_by")
    autocomplete_fields = ("organization", "knowledge_base", "uploaded_by")
    readonly_fields = ("checksum", "storage_key", "chunk_count", "page_count", "created_at", "updated_at")


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "index", "page_number", "char_count", "is_flagged", "created_at")
    list_filter = ("is_flagged",)
    search_fields = ("document__title",)
    list_select_related = ("organization", "knowledge_base", "document")
    readonly_fields = (
        "organization",
        "knowledge_base",
        "document",
        "index",
        "content",
        "char_count",
        "page_number",
        "is_flagged",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(DocumentProcessingJob)
class DocumentProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("document", "status", "attempts", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("document__title", "celery_task_id")
    list_select_related = ("document",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(RetrievalRecord)
class RetrievalRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "assistant", "retriever", "result_count", "latency_ms")
    list_filter = ("retriever",)
    search_fields = ("assistant__name",)
    list_select_related = ("organization", "assistant")
    date_hierarchy = "created_at"
    readonly_fields = (
        "organization",
        "assistant",
        "conversation_id",
        "query_hash",
        "query_length",
        "chunk_ids",
        "scores",
        "retriever",
        "result_count",
        "latency_ms",
        "created_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

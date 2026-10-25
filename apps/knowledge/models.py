"""Knowledge base models.

Every row carries ``organization`` directly. Redundant denormalisation is
deliberate: retrieval queries can filter by tenant without joining through the
knowledge base, which makes accidental cross-tenant reads far less likely and
keeps the hot path cheap.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import VectorField

from apps.common.models import SoftDeleteModel, TimeStampedModel, UUIDModel


class KnowledgeBase(UUIDModel, TimeStampedModel):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="knowledge_bases"
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "knowledge_base"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="uniq_kb_slug_per_org")
        ]
        indexes = [models.Index(fields=["organization", "name"], name="kb_org_name_idx")]

    def __str__(self) -> str:
        return self.name


class Document(UUIDModel, TimeStampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="documents"
    )
    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)

    # Server-generated storage key; the client-supplied filename is never used
    # as a path component.
    storage_key = models.CharField(max_length=512)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    error_code = models.CharField(max_length=48, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    page_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "knowledge_document"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "status"], name="doc_org_status_idx"),
            models.Index(fields=["knowledge_base", "-created_at"], name="doc_kb_created_idx"),
            models.Index(fields=["organization", "-created_at"], name="doc_org_created_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class DocumentChunk(UUIDModel):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="document_chunks"
    )
    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="chunks")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    index = models.PositiveIntegerField()
    content = models.TextField()
    char_count = models.PositiveIntegerField(default=0)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    is_flagged = models.BooleanField(
        default=False, help_text="Chunk contained instruction-like text and was neutralised."
    )
    search_vector = SearchVectorField(null=True)
    embedding = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "knowledge_document_chunk"
        ordering = ("document_id", "index")
        constraints = [
            models.UniqueConstraint(fields=["document", "index"], name="uniq_chunk_index_per_document")
        ]
        indexes = [
            GinIndex(fields=["search_vector"], name="chunk_search_vector_idx"),
            models.Index(fields=["organization", "knowledge_base"], name="chunk_org_kb_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}#{self.index}"


class DocumentProcessingJob(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=48, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "knowledge_processing_job"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["document", "-created_at"], name="job_document_idx")]


class RetrievalRecord(UUIDModel):
    """Provenance for a retrieval operation (which chunks were used for an answer)."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="retrieval_records"
    )
    assistant = models.ForeignKey(
        "assistants.Assistant", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    conversation_id = models.UUIDField(null=True, blank=True, db_index=True)
    query_hash = models.CharField(max_length=64, db_index=True)
    query_length = models.PositiveIntegerField(default=0)
    chunk_ids = models.JSONField(default=list)
    scores = models.JSONField(default=list)
    retriever = models.CharField(max_length=32, blank=True)
    result_count = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "knowledge_retrieval_record"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["organization", "-created_at"], name="retrieval_org_idx")]

"""Knowledge base and document services."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.billing.services import assert_can_store_knowledge
from apps.common.exceptions import APIError
from apps.knowledge.ingestion import enqueue_ingestion
from apps.knowledge.models import Document, DocumentChunk, DocumentProcessingJob, KnowledgeBase
from apps.knowledge.storage import build_storage_key, validate_upload

logger = logging.getLogger("apps.knowledge.services")


def unique_kb_slug(organization, name: str) -> str:
    base = slugify(name)[:140] or "knowledge-base"
    candidate = base
    suffix = 2
    while KnowledgeBase.objects.filter(organization=organization, slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_knowledge_base(*, organization, name: str, description: str = "", actor=None) -> KnowledgeBase:
    knowledge_base = KnowledgeBase.objects.create(
        organization=organization,
        name=name,
        slug=unique_kb_slug(organization, name),
        description=description,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.KNOWLEDGE_CREATED,
        organization=organization,
        actor_user=actor,
        target_type="knowledge_base",
        target_id=knowledge_base.id,
        metadata={"name": name},
    )
    return knowledge_base


def _checksum(upload) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for block in iter(lambda: upload.read(65536), b""):
        digest.update(block)
    upload.seek(0)
    return digest.hexdigest()


@transaction.atomic
def create_document(*, organization, knowledge_base, upload, title: str = "", actor=None) -> Document:
    validated = validate_upload(upload)
    assert_can_store_knowledge(organization, additional_bytes=validated.size_bytes)

    storage_key = build_storage_key(organization_id=organization.id, extension=validated.extension)
    saved_key = default_storage.save(storage_key, upload)
    checksum = _checksum(upload)

    document = Document.objects.create(
        organization=organization,
        knowledge_base=knowledge_base,
        title=(title or Path(upload.name).stem)[:255],
        original_filename=Path(upload.name).name[:255],
        content_type=validated.content_type,
        size_bytes=validated.size_bytes,
        checksum=checksum,
        storage_key=saved_key,
        status=Document.Status.PENDING,
        uploaded_by=actor if getattr(actor, "pk", None) else None,
    )

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.DOCUMENT_UPLOADED,
        organization=organization,
        actor_user=actor,
        target_type="document",
        target_id=document.id,
        metadata={"name": document.title, "knowledge_base_id": str(knowledge_base.id)},
    )
    return document


def delete_document(document: Document, *, actor=None) -> None:
    if document.deleted_at is not None:
        return
    storage_key = document.storage_key
    DocumentChunk.objects.filter(document=document).delete()
    document.deleted_at = timezone.now()
    document.status = Document.Status.FAILED
    document.error_code = "deleted"
    document.save(update_fields=["deleted_at", "status", "error_code", "updated_at"])

    try:
        default_storage.delete(storage_key)
    except Exception:  # pragma: no cover - storage outage
        logger.warning("Unable to delete stored document file")

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.DOCUMENT_DELETED,
        organization=document.organization,
        actor_user=actor,
        target_type="document",
        target_id=document.id,
    )


def reprocess_document(document: Document, *, actor=None) -> DocumentProcessingJob:
    if document.deleted_at is not None:
        raise APIError("This document has been deleted.", code="document_deleted")

    DocumentChunk.objects.filter(document=document).delete()
    Document.objects.filter(pk=document.pk).update(
        status=Document.Status.PENDING,
        chunk_count=0,
        error_code="",
        error_message="",
        updated_at=timezone.now(),
    )
    job = DocumentProcessingJob.objects.create(document=document)

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.DOCUMENT_REPROCESSED,
        organization=document.organization,
        actor_user=actor,
        target_type="document",
        target_id=document.id,
    )
    return job


def processing_status(document: Document) -> dict:
    job = document.jobs.order_by("-created_at").first()
    return {
        "document_id": str(document.id),
        "status": document.status,
        "chunk_count": document.chunk_count,
        "page_count": document.page_count,
        "error_code": document.error_code,
        "error_message": document.error_message,
        "job": {
            "id": str(job.id) if job else None,
            "status": job.status if job else None,
            "attempts": job.attempts if job else 0,
            "started_at": job.started_at if job else None,
            "finished_at": job.finished_at if job else None,
        },
    }


def knowledge_storage_summary(organization) -> dict:
    from django.db.models import Sum

    total = (
        Document.objects.filter(organization=organization, deleted_at__isnull=True).aggregate(
            total=Sum("size_bytes")
        )["total"]
        or 0
    )
    return {
        "used_bytes": total,
        "document_count": Document.objects.filter(
            organization=organization, deleted_at__isnull=True
        ).count(),
        "max_bytes": settings.KNOWLEDGE_MAX_FILE_BYTES,
    }


__all__ = [
    "create_document",
    "create_knowledge_base",
    "delete_document",
    "enqueue_ingestion",
    "knowledge_storage_summary",
    "processing_status",
    "reprocess_document",
    "unique_kb_slug",
]

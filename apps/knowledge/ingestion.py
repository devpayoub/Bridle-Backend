"""Document ingestion pipeline.

Runs outside request/response when Celery is available, but the service itself is
synchronous and idempotent so tests (and a ``--eager`` deployment) can call it
directly.

Long steps (extraction, embedding) never run inside a database transaction; only
the final persistence step is atomic.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.utils import timezone

from apps.ai_gateway.base import ProviderError
from apps.knowledge.chunking import chunk_segments
from apps.knowledge.extraction import ExtractionError, extract_document
from apps.knowledge.models import Document, DocumentChunk, DocumentProcessingJob
from apps.knowledge.sanitization import sanitize_text

logger = logging.getLogger("apps.knowledge.ingestion")

MAX_ATTEMPTS = 3


def enqueue_ingestion(document: Document, *, job: DocumentProcessingJob | None = None) -> DocumentProcessingJob:
    """Create a job row and hand the work to Celery."""
    job = job or DocumentProcessingJob.objects.create(document=document)
    try:
        from apps.knowledge.tasks import process_document

        async_result = process_document.delay(str(document.id), str(job.id))
        DocumentProcessingJob.objects.filter(pk=job.pk).update(celery_task_id=getattr(async_result, "id", "") or "")
    except Exception:
        # Broker unavailable: leave the job QUEUED so an operator/beat task can
        # retry, and surface the condition in logs.
        logger.exception("Unable to enqueue document processing job=%s", job.id)
    return job


def _set_running(document: Document, job: DocumentProcessingJob) -> None:
    Document.objects.filter(pk=document.pk).update(
        status=Document.Status.PROCESSING, error_code="", error_message="", updated_at=timezone.now()
    )
    DocumentProcessingJob.objects.filter(pk=job.pk).update(
        status=DocumentProcessingJob.Status.RUNNING,
        attempts=job.attempts + 1,
        started_at=timezone.now(),
    )


def _mark_failed(document: Document, job: DocumentProcessingJob, code: str, message: str) -> None:
    Document.objects.filter(pk=document.pk).update(
        status=Document.Status.FAILED,
        error_code=code[:48],
        error_message=message[:500],
        updated_at=timezone.now(),
    )
    DocumentProcessingJob.objects.filter(pk=job.pk).update(
        status=DocumentProcessingJob.Status.FAILED,
        error_code=code[:48],
        error_message=message[:500],
        finished_at=timezone.now(),
    )


def _embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.VECTOR_SEARCH_ENABLED or not texts:
        return None
    from apps.ai_gateway.registry import get_embedding_provider

    provider = get_embedding_provider()
    vectors: list[list[float]] = []
    batch_size = settings.KNOWLEDGE_EMBEDDING_BATCH_SIZE
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        result = provider.create_embeddings(texts=batch)
        vectors.extend(result.vectors)
    return vectors


@transaction.atomic
def _persist(
    document: Document,
    job: DocumentProcessingJob,
    prepared: list[tuple[int, str, bool, int | None]],
    embeddings: list[list[float]] | None,
    page_count: int,
) -> None:
    DocumentChunk.objects.filter(document=document).delete()

    rows = []
    for position, (original_index, text, flagged, page_number) in enumerate(prepared):
        rows.append(
            DocumentChunk(
                organization_id=document.organization_id,
                knowledge_base_id=document.knowledge_base_id,
                document=document,
                index=position,
                content=text,
                char_count=len(text),
                page_number=page_number,
                is_flagged=flagged,
                embedding=embeddings[position] if embeddings else None,
            )
        )
    DocumentChunk.objects.bulk_create(rows, batch_size=500)

    # Full-text vectors are computed in the database to keep tokenisation
    # consistent with query-time tokenisation.
    DocumentChunk.objects.filter(document=document).update(
        search_vector=SearchVector("content", config=settings.KNOWLEDGE_SEARCH_CONFIG)
    )

    Document.objects.filter(pk=document.pk).update(
        status=Document.Status.READY,
        chunk_count=len(rows),
        page_count=page_count,
        error_code="",
        error_message="",
        updated_at=timezone.now(),
    )
    DocumentProcessingJob.objects.filter(pk=job.pk).update(
        status=DocumentProcessingJob.Status.SUCCEEDED,
        finished_at=timezone.now(),
        error_code="",
        error_message="",
    )


def ingest_document(document_id: str, job_id: str | None = None) -> None:
    """Process one document. Safe to call twice: chunk rows are replaced."""
    document = (
        Document.objects.select_related("knowledge_base")
        .filter(pk=document_id, deleted_at__isnull=True)
        .first()
    )
    if document is None:
        logger.warning("Ingestion skipped: document not found id=%s", document_id)
        return

    job = DocumentProcessingJob.objects.filter(pk=job_id).first() if job_id else None
    if job is None:
        job = DocumentProcessingJob.objects.create(document=document)

    if job.attempts >= MAX_ATTEMPTS and job.status == DocumentProcessingJob.Status.FAILED:
        logger.warning("Ingestion skipped: attempt limit reached document=%s", document.id)
        return

    _set_running(document, job)

    try:
        path = Path(settings.MEDIA_ROOT) / document.storage_key
        if not path.exists():
            raise ExtractionError("The stored file is unavailable.", code="file_missing")

        extracted = extract_document(path)
        raw_chunks = chunk_segments(
            extracted.segments,
            chunk_size=settings.KNOWLEDGE_CHUNK_SIZE,
            overlap=settings.KNOWLEDGE_CHUNK_OVERLAP,
        )
        if not raw_chunks:
            raise ExtractionError("No usable content was found in the document.", code="empty_document")

        prepared: list[tuple[int, str, bool, int | None]] = []
        for chunk in raw_chunks:
            cleaned, flagged = sanitize_text(chunk.text)
            if not cleaned.strip():
                continue
            prepared.append((chunk.index, cleaned, flagged, chunk.page_number))

        if not prepared:
            raise ExtractionError("No usable content was found in the document.", code="empty_document")

        embeddings = _embed([item[1] for item in prepared])
        _persist(document, job, prepared, embeddings, extracted.page_count)
        logger.info(
            "Document ingested id=%s chunks=%s flagged=%s",
            document.id,
            len(prepared),
            sum(1 for item in prepared if item[2]),
        )

    except ExtractionError as exc:
        logger.warning("Document ingestion failed id=%s code=%s", document.id, exc.code)
        _mark_failed(document, job, exc.code, str(exc))
    except ProviderError as exc:
        logger.warning("Embedding provider failed document=%s code=%s", document.id, exc.code)
        _mark_failed(document, job, "embedding_failed", "Embedding generation failed. Retry processing.")
    except Exception:
        logger.exception("Unexpected ingestion failure document=%s", document.id)
        _mark_failed(document, job, "processing_error", "The document could not be processed.")

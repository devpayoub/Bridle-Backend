"""Celery tasks for knowledge ingestion."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.knowledge.ingestion import ingest_document

logger = logging.getLogger("apps.knowledge.tasks")


@shared_task(
    name="knowledge.process_document",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_document(self, document_id: str, job_id: str | None = None) -> str:
    """Idempotent ingestion task; the service replaces chunks on each run."""
    ingest_document(document_id, job_id)
    return document_id

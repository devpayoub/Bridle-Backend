"""Audit retention task."""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.audit.models import AuditLog

logger = logging.getLogger("apps.audit.tasks")


@shared_task(name="audit.purge_expired")
def purge_expired_audit_logs(days: int | None = None) -> int:
    """Delete audit rows older than the configured retention window."""
    retention_days = days or settings.AUDIT_LOG_RETENTION_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=retention_days)
    deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        logger.info("Purged %s audit rows older than %s days", deleted, retention_days)
    return deleted

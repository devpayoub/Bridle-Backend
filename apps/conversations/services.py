"""Conversation lifecycle services (anonymisation and retention)."""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.conversations.models import Conversation, Message

logger = logging.getLogger("apps.conversations")


@transaction.atomic
def anonymize_conversation(conversation: Conversation, *, actor=None, reason: str = "user_request") -> None:
    """Irreversibly remove message content while keeping aggregate history.

    Token counts and timestamps are preserved for usage accounting; content,
    citations, provider metadata and the external identifier are erased.
    """
    Conversation.objects.filter(pk=conversation.pk).update(
        status=Conversation.Status.ANONYMIZED,
        external_user_id="",
        metadata={},
        updated_at=timezone.now(),
    )
    Message.objects.filter(conversation=conversation).update(
        content="[redacted]",
        citations=[],
        provider_metadata={},
    )

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.CONVERSATION_DELETED,
        organization=conversation.organization,
        actor_user=actor,
        target_type="conversation",
        target_id=conversation.id,
        metadata={"reason": reason},
    )
    logger.info("Conversation anonymised id=%s reason=%s", conversation.id, reason)


def expired_conversations(*, batch_size: int = 200):
    return Conversation.objects.filter(
        retention_expires_at__lt=timezone.now(),
        status__in=[Conversation.Status.ACTIVE, Conversation.Status.CLOSED],
    ).order_by("retention_expires_at")[:batch_size]

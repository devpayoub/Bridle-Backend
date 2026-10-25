"""Scheduled conversation retention tasks."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.conversations.services import anonymize_conversation, expired_conversations

logger = logging.getLogger("apps.conversations.tasks")


@shared_task(name="conversations.purge_expired")
def purge_expired_conversations(batch_size: int = 200) -> int:
    """Anonymise conversations past their retention window.

    Runs in batches so a large backlog cannot hold a long transaction or block
    chat traffic. Idempotent: anonymised rows are excluded from the query.
    """
    processed = 0
    while True:
        batch = list(expired_conversations(batch_size=batch_size))
        if not batch:
            break
        for conversation in batch:
            anonymize_conversation(conversation, reason="retention")
            processed += 1
        if len(batch) < batch_size:
            break
    if processed:
        logger.info("Retention purge anonymised %s conversations", processed)
    return processed

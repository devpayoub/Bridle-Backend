"""Celery tasks for channels."""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.channels.models import ChannelEvent, ChannelIdentity

logger = logging.getLogger("apps.channels.tasks")


def enqueue_channel_event(event: ChannelEvent, payload: dict) -> None:
    """Hand the update to a worker. The payload rides in the task args, not the DB.

    A broker outage must not lose the webhook acknowledgement: the row stays
    RECEIVED and the sweeper reports it later.
    """
    try:
        from apps.channels.tasks import process_telegram_update

        process_telegram_update.delay(str(event.id), payload)
        ChannelEvent.objects.filter(pk=event.pk).update(enqueued_at=timezone.now())
    except Exception:  # noqa: BLE001 - never fail the webhook because the broker is down
        logger.exception("channel.enqueue_failed event=%s", event.id)


@shared_task(
    name="channels.telegram.process_update",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    acks_late=True,
)
def process_telegram_update(self, event_id: str, payload: dict) -> str:
    from apps.channels.dispatch import handle_inbound_event
    from apps.channels.telegram.client import (
        TelegramRateLimited,
        TelegramUnauthorized,
        TelegramUnavailable,
    )

    try:
        handle_inbound_event(event_id, payload)
    except TelegramRateLimited as exc:
        raise self.retry(countdown=min(exc.retry_after, 300)) from None
    except TelegramUnavailable:
        raise self.retry(countdown=15) from None
    except TelegramUnauthorized:
        # A revoked token never recovers: mark the channel so an operator sees it.
        from apps.channels.models import Channel

        ChannelEvent.objects.filter(pk=event_id).update(
            status=ChannelEvent.Status.FAILED, error_code="telegram_unauthorized"
        )
        Channel.objects.filter(events__id=event_id).update(status=Channel.Status.ERROR, webhook_error="telegram_unauthorized")
        logger.warning("channel.telegram.unauthorized event=%s", event_id)
    return event_id


@shared_task(name="channels.purge_events")
def purge_events(days: int | None = None) -> int:
    """Drop the delivery ledger. Idempotent: deleting an already-deleted row is a no-op."""
    retention = days or settings.CHANNELS_EVENT_RETENTION_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=retention)
    deleted, _ = ChannelEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted


@shared_task(name="channels.purge_identities")
def purge_identities(days: int | None = None) -> int:
    """Forget external users who have not written in for a while."""
    retention = days or settings.CHANNELS_IDENTITY_RETENTION_DAYS
    cutoff = timezone.now() - timezone.timedelta(days=retention)
    deleted, _ = ChannelIdentity.objects.filter(last_seen_at__lt=cutoff).delete()
    return deleted


@shared_task(name="channels.requeue_stale_events")
def requeue_stale_events(minutes: int = 10) -> int:
    """Recover events a dead worker left behind.

    The payload is not stored, so a stale RECEIVED row cannot be replayed — it is
    failed explicitly instead of sitting in the queue forever.
    """
    cutoff = timezone.now() - timezone.timedelta(minutes=minutes)
    stale = ChannelEvent.objects.filter(status=ChannelEvent.Status.RECEIVED, created_at__lt=cutoff)
    count = stale.update(status=ChannelEvent.Status.FAILED, error_code="not_enqueued")
    stuck = ChannelEvent.objects.filter(status=ChannelEvent.Status.PROCESSING, created_at__lt=cutoff)
    count += stuck.update(status=ChannelEvent.Status.FAILED, error_code="worker_lost")
    if count:
        logger.warning("channel.events.recovered count=%s", count)
    return count

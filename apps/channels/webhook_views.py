"""Inbound webhook: unauthenticated, unguessable, and deliberately boring.

Order matters. The body cap comes before parsing; the channel lookup and the path
key compare come before anything that could reveal whether a channel exists; the
header secret is checked before a single row is written. The view only ever
enqueues work — it never calls the model.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError
from django.http import Http404
from django.utils.crypto import constant_time_compare
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.channels.models import Channel, ChannelEvent
from apps.channels.registry import UnsupportedChannelType, get_adapter
from apps.channels.tasks import enqueue_channel_event
from apps.common.exceptions import RateLimitExceeded
from apps.usage.rate_limit import enforce_rate_limit

logger = logging.getLogger("apps.channels.webhook")


def _path_key_ok(channel: Channel, presented: str) -> bool:
    stored = channel.get_webhook_key()
    if not stored:
        logger.error("channel.webhook.key_unreadable channel=%s", channel.id)
        return False
    return constant_time_compare(stored, presented or "")


class ChannelWebhookView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []
    schema = None  # never appears in the public API schema

    def post(self, request, channel_type: str, channel_id, path_key: str):
        if not settings.CHANNELS_ENABLED:
            raise Http404

        # Django exposes the length only in META, and it may be absent or bogus.
        try:
            declared_length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > settings.CHANNELS_MAX_BODY_BYTES:
            return Response({"ok": False}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        channel = (
            Channel.objects.select_related("organization", "assistant")
            .filter(id=channel_id, type=channel_type.upper())
            .first()
        )
        # Uniform 404 for "no such channel" and "wrong key": no enumeration oracle.
        if channel is None or not _path_key_ok(channel, path_key):
            return Response({"ok": False}, status=status.HTTP_404_NOT_FOUND)

        # Acknowledging a disabled channel stops Telegram retrying forever.
        if channel.status != Channel.Status.ACTIVE:
            return Response({"ok": True})

        try:
            adapter = get_adapter(channel.type)
        except UnsupportedChannelType:
            return Response({"ok": True})

        if not adapter.verify_request(channel, request):
            logger.warning("channel.webhook.bad_secret channel=%s", channel.id)
            return Response({"ok": False}, status=status.HTTP_403_FORBIDDEN)

        # Only after the request is proven authentic do we let it touch the cache.
        try:
            enforce_rate_limit("channel.webhook", f"ch:{channel.id}")
        except RateLimitExceeded:
            return Response({"ok": True})

        payload = request.data if isinstance(request.data, dict) else {}
        update_id = payload.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            return Response({"ok": False}, status=status.HTTP_400_BAD_REQUEST)

        kind = (
            ChannelEvent.Kind.MESSAGE
            if isinstance(payload.get("message"), dict)
            else ChannelEvent.Kind.OTHER
        )
        try:
            event, created = ChannelEvent.objects.get_or_create(
                channel=channel,
                update_id=update_id,
                defaults={
                    "organization_id": channel.organization_id,
                    "kind": kind,
                    "status": (
                        ChannelEvent.Status.RECEIVED
                        if kind == ChannelEvent.Kind.MESSAGE
                        else ChannelEvent.Status.IGNORED
                    ),
                },
            )
        except IntegrityError:
            # Lost a race with the same update — treat as a duplicate.
            created = False

        if created and kind == ChannelEvent.Kind.MESSAGE:
            enqueue_channel_event(event, payload)

        return Response({"ok": True})

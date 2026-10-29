"""Public webhook URLs.

Mounted at the root, not under ``/api/v1/``: the request-path logger only records
``/api/`` paths, and the path key must never end up in an access log.
"""
from __future__ import annotations

from django.urls import path

from apps.channels.webhook_views import ChannelWebhookView

app_name = "channels_webhook"

urlpatterns = [
    path(
        "<str:channel_type>/<uuid:channel_id>/<str:path_key>/",
        ChannelWebhookView.as_view(),
        name="channel-webhook",
    ),
]

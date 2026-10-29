"""Webhook security: who can post, what gets processed, what never reaches the model."""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import Channel, ChannelEvent

pytestmark = pytest.mark.django_db

SECRET = "secret-token-abc"
PATH_KEY = "path-key-123"
BOT_TOKEN = "8123456789:" + "A" * 35


@pytest.fixture(autouse=True)
def channels_enabled(settings):
    settings.CHANNELS_ENABLED = True
    settings.CHANNELS_PUBLIC_BASE_URL = "https://api.example.com"
    return settings


@pytest.fixture
def channel(organization, active_assistant):
    channel = Channel.objects.create(
        organization=organization,
        assistant=active_assistant,
        type=Channel.Type.TELEGRAM,
        name="Test bot",
        status=Channel.Status.ACTIVE,
        external_id="8123456789",
        external_username="test_bot",
    )
    channel.set_credentials(BOT_TOKEN)
    channel.set_webhook_key(PATH_KEY)
    channel.set_webhook_secret(SECRET)
    channel.save()
    return channel


@pytest.fixture
def no_outbound():
    """The dispatch path would otherwise call api.telegram.org."""
    with patch(
        "apps.channels.adapters.telegram.TelegramAdapter.send_text", return_value=["1"]
    ) as patched:
        yield patched


def update_body(update_id: int = 1001, text: str = "What time is check-in?") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "date": int(time.time()),
            "from": {"id": 4242, "is_bot": False},
            "chat": {"id": 4242, "type": "private"},
            "text": text,
        },
    }


def webhook_url(channel: Channel, *, key: str = PATH_KEY, channel_id=None) -> str:
    return reverse(
        "channels_webhook:channel-webhook",
        kwargs={
            "channel_type": "telegram",
            "channel_id": channel_id or channel.id,
            "path_key": key,
        },
    )


class TestAuthenticity:
    def test_valid_update_is_acknowledged(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert ChannelEvent.objects.filter(channel=channel, update_id=1001).exists()

    def test_missing_secret_header_is_rejected(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body()),
            content_type="application/json",
        )
        assert response.status_code == 403
        assert not ChannelEvent.objects.filter(channel=channel).exists()

    def test_wrong_secret_header_is_rejected(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="not-the-secret",
        )
        assert response.status_code == 403
        assert not ChannelEvent.objects.filter(channel=channel).exists()

    def test_unknown_channel_is_404(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel, channel_id="00000000-0000-0000-0000-000000000000"),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 404

    def test_wrong_path_key_is_404(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel, key="wrong-key"),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 404

    def test_channels_disabled_returns_404(self, api_client, channel, settings, no_outbound):
        settings.CHANNELS_ENABLED = False
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 404


class TestReplay:
    def test_replayed_update_is_processed_once(self, api_client, channel, no_outbound):
        body = json.dumps(update_body(update_id=2002))
        headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": SECRET}

        first = api_client.post(webhook_url(channel), data=body, content_type="application/json", **headers)
        second = api_client.post(webhook_url(channel), data=body, content_type="application/json", **headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert ChannelEvent.objects.filter(channel=channel, update_id=2002).count() == 1

    def test_missing_update_id_is_rejected(self, api_client, channel, no_outbound):
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps({"message": {"text": "hi"}}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 400


class TestLifecycle:
    def test_disabled_channel_acks_without_processing(self, api_client, channel, no_outbound):
        channel.status = Channel.Status.DISABLED
        channel.save(update_fields=["status"])

        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body(update_id=3003)),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 200
        assert not ChannelEvent.objects.filter(channel=channel).exists()
        assert no_outbound.called is False

    def test_oversized_body_is_rejected(self, api_client, channel, settings, no_outbound):
        settings.CHANNELS_MAX_BODY_BYTES = 10
        response = api_client.post(
            webhook_url(channel),
            data=json.dumps(update_body()),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )
        assert response.status_code == 413


class TestSecretStorage:
    def test_token_is_encrypted_at_rest(self, channel):
        channel.refresh_from_db()
        assert channel.credentials_ciphertext
        assert BOT_TOKEN not in channel.credentials_ciphertext
        assert channel.credentials_last4 == BOT_TOKEN[-4:]
        assert channel.get_credentials() == BOT_TOKEN

    def test_webhook_secret_is_never_stored_in_plaintext(self, channel):
        channel.refresh_from_db()
        assert channel.webhook_secret_digest
        assert SECRET not in channel.webhook_secret_digest
        assert channel.verify_webhook_secret(SECRET) is True
        assert channel.verify_webhook_secret("nope") is False

    def test_webhook_path_key_is_readable_for_display(self, channel):
        channel.refresh_from_db()
        assert channel.get_webhook_key() == PATH_KEY

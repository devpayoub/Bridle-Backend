"""Dispatch behaviour: what the customer actually receives in chat."""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import Channel, ChannelEvent, ChannelThread
from apps.conversations.models import Conversation

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
def sent():
    """Capture outbound messages instead of calling Telegram."""
    with patch(
        "apps.channels.adapters.telegram.TelegramAdapter.send_text", return_value=["1"]
    ) as patched:
        yield patched


def deliver(api_client, channel, text: str, update_id: int = 1001):
    body = {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "date": int(time.time()),
            "from": {"id": 4242, "is_bot": False},
            "chat": {"id": 4242, "type": "private"},
            "text": text,
        },
    }
    url = reverse(
        "channels_webhook:channel-webhook",
        kwargs={"channel_type": "telegram", "channel_id": channel.id, "path_key": PATH_KEY},
    )
    return api_client.post(
        url,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
    )


def last_reply(sent) -> str:
    assert sent.called, "nothing was sent"
    args = sent.call_args.args
    parts = args[2]
    return "\n".join(parts)


class TestReplies:
    def test_allowed_message_is_answered_and_persisted(self, api_client, channel, sent):
        assert deliver(api_client, channel, "What time is check-in?").status_code == 200

        event = ChannelEvent.objects.get(channel=channel)
        assert event.status == ChannelEvent.Status.REPLIED
        assert event.reply_message_ids == ["1"]
        assert ChannelThread.objects.filter(channel=channel, external_thread_id="4242").exists()
        assert Conversation.objects.filter(organization=channel.organization).exists()

    def test_out_of_domain_is_refused_without_a_model_call(self, api_client, channel, sent):
        from apps.ai_gateway.registry import get_provider

        provider = get_provider("mock")
        provider.calls.clear()

        deliver(api_client, channel, "Who won the World Cup final?", update_id=1002)

        assert provider.calls == []
        assert last_reply(sent) == channel.assistant.fallback_message

    def test_media_message_never_reaches_the_model(self, api_client, channel, sent):
        from apps.ai_gateway.registry import get_provider

        provider = get_provider("mock")
        provider.calls.clear()

        body = {
            "update_id": 1003,
            "message": {
                "message_id": 6,
                "date": int(time.time()),
                "from": {"id": 4242, "is_bot": False},
                "chat": {"id": 4242, "type": "private"},
                "photo": [{"file_id": "x"}],
            },
        }
        url = reverse(
            "channels_webhook:channel-webhook",
            kwargs={"channel_type": "telegram", "channel_id": channel.id, "path_key": PATH_KEY},
        )
        api_client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET,
        )

        assert provider.calls == []
        assert "text messages" in last_reply(sent)
        assert ChannelEvent.objects.get(channel=channel, update_id=1003).status == ChannelEvent.Status.IGNORED

    def test_second_message_reuses_the_conversation(self, api_client, channel, sent):
        deliver(api_client, channel, "What time is check-in?", update_id=1004)
        deliver(api_client, channel, "And breakfast?", update_id=1005)

        thread = ChannelThread.objects.get(channel=channel, external_thread_id="4242")
        assert Conversation.objects.filter(organization=channel.organization).count() == 1
        assert thread.conversation_id is not None

    def test_anonymised_conversation_starts_a_fresh_one(self, api_client, channel, sent):
        deliver(api_client, channel, "What time is check-in?", update_id=1006)
        thread = ChannelThread.objects.get(channel=channel, external_thread_id="4242")
        Conversation.objects.filter(pk=thread.conversation_id).update(
            status=Conversation.Status.ANONYMIZED
        )

        deliver(api_client, channel, "Hello again", update_id=1007)

        thread.refresh_from_db()
        assert thread.conversation is not None
        assert thread.conversation.status != Conversation.Status.ANONYMIZED


class TestGuards:
    def test_suspended_workspace_does_not_answer(self, api_client, channel, sent):
        channel.organization.status = "SUSPENDED"
        channel.organization.save(update_fields=["status"])

        deliver(api_client, channel, "What time is check-in?", update_id=1008)

        assert sent.called is False
        event = ChannelEvent.objects.get(channel=channel, update_id=1008)
        assert event.status == ChannelEvent.Status.IGNORED
        assert event.error_code == "organization_inactive"

    def test_identity_rate_limit_sends_a_slow_down(self, api_client, channel, sent, settings):
        settings.RATE_LIMITS = {
            **settings.RATE_LIMITS,
            "channel.chat.identity": {"limit": 1, "window": 60},
        }

        deliver(api_client, channel, "What time is check-in?", update_id=1009)
        deliver(api_client, channel, "Hello?", update_id=1010)

        event = ChannelEvent.objects.get(channel=channel, update_id=1010)
        assert event.status == ChannelEvent.Status.IGNORED
        assert event.error_code == "rate_limited"

    def test_disabled_channel_is_not_processed(self, api_client, channel, sent):
        channel.status = Channel.Status.DISABLED
        channel.save(update_fields=["status"])

        deliver(api_client, channel, "Hello", update_id=1011)
        assert not ChannelEvent.objects.filter(channel=channel, update_id=1011).exists()


class TestFormatting:
    def test_long_answer_is_split_within_the_limit(self, api_client, channel, sent, settings):
        settings.CHANNELS_TELEGRAM_MAX_MESSAGE_CHARS = 200
        settings.CHANNELS_TELEGRAM_MAX_PARTS = 3

        with patch(
            "apps.channels.dispatch.render_reply",
            return_value=["a" * 200, "b" * 200],
        ):
            deliver(api_client, channel, "What time is check-in?", update_id=1012)

        assert sent.call_args.args[2] == ["a" * 200, "b" * 200]

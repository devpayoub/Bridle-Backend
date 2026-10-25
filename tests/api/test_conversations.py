"""Conversation history and retention API tests."""
from __future__ import annotations

import pytest

from apps.conversations.models import Conversation, Message

CHAT_URL = "/api/v1/chat/completions/"
CONVERSATIONS_URL = "/api/v1/conversations/"


@pytest.fixture
def conversation(api_key_client, active_assistant):
    response = api_key_client.post(
        CHAT_URL,
        {"assistant_id": str(active_assistant.id), "message": "What time is check-in?"},
        format="json",
    )
    return Conversation.objects.get(id=response.data["conversation_id"])


@pytest.mark.django_db
class TestConversationHistory:
    def test_list_conversations(self, auth_client, conversation):
        response = auth_client.get(CONVERSATIONS_URL)
        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["message_count"] == 2

    def test_filter_by_assistant(self, auth_client, conversation, active_assistant):
        response = auth_client.get(CONVERSATIONS_URL, {"assistant_id": str(active_assistant.id)})
        assert response.data["count"] == 1

    def test_retrieve_conversation(self, auth_client, conversation):
        response = auth_client.get(f"{CONVERSATIONS_URL}{conversation.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(conversation.id)

    def test_list_messages(self, auth_client, conversation):
        response = auth_client.get(f"{CONVERSATIONS_URL}{conversation.id}/messages/")
        assert response.status_code == 200
        assert response.data["count"] == 2
        roles = [row["role"] for row in response.data["results"]]
        assert roles == ["USER", "ASSISTANT"]

    def test_messages_do_not_expose_system_prompt(self, auth_client, conversation):
        response = auth_client.get(f"{CONVERSATIONS_URL}{conversation.id}/messages/")
        joined = " ".join(row["content"] for row in response.data["results"])
        assert "Non-negotiable platform rules" not in joined

    def test_anonymise_conversation(self, auth_client, conversation):
        response = auth_client.delete(f"{CONVERSATIONS_URL}{conversation.id}/")
        assert response.status_code == 204

        conversation.refresh_from_db()
        assert conversation.status == Conversation.Status.ANONYMIZED
        assert conversation.external_user_id == ""
        assert all(message.content == "[redacted]" for message in conversation.messages.all())

    def test_foreign_conversation_is_404(self, other_auth_client, conversation):
        assert other_auth_client.get(f"{CONVERSATIONS_URL}{conversation.id}/").status_code == 404

    def test_read_only_cannot_anonymise(self, organization, conversation, membership_factory):
        from rest_framework.test import APIClient

        _, read_only = membership_factory(organization, role="READ_ONLY")
        client = APIClient()
        client.force_authenticate(user=read_only)
        client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        assert client.delete(f"{CONVERSATIONS_URL}{conversation.id}/").status_code == 403


@pytest.mark.django_db
def test_conversation_records_configuration_version(conversation, active_assistant):
    assert conversation.config_version == active_assistant.config_version


@pytest.mark.django_db
def test_messages_store_policy_decision(conversation):
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.policy_decision == "ALLOW"
    assert assistant_message.provider_metadata["provider"] == "mock"

"""Usage reporting API tests."""
from __future__ import annotations

import pytest

SUMMARY_URL = "/api/v1/usage/summary/"
BY_ASSISTANT_URL = "/api/v1/usage/by-assistant/"
BY_API_KEY_URL = "/api/v1/usage/by-api-key/"
CHAT_URL = "/api/v1/chat/completions/"


def chat(client, assistant, message="What time is check-in?"):
    return client.post(
        CHAT_URL, {"assistant_id": str(assistant.id), "message": message}, format="json"
    )


@pytest.mark.django_db
class TestUsageReporting:
    def test_summary_counts_requests_and_quota(self, api_key_client, active_assistant, auth_client):
        chat(api_key_client, active_assistant)
        chat(api_key_client, active_assistant, "Who won the World Cup?")

        response = auth_client.get(SUMMARY_URL)
        assert response.status_code == 200
        totals = response.data["totals"]
        assert totals["requests"] == 2
        assert totals["successful"] == 1
        assert totals["refused"] == 1
        assert totals["input_tokens"] > 0
        assert response.data["quota"]["chat_requests"]["used"] == 1
        assert response.data["quota"]["chat_requests"]["limit"] > 0
        assert response.data["by_kind"]["CHAT"] == 2

    def test_by_assistant_groups_results(self, api_key_client, active_assistant, auth_client):
        chat(api_key_client, active_assistant)
        response = auth_client.get(BY_ASSISTANT_URL)
        assert response.status_code == 200
        assert response.data["results"][0]["assistant_id"] == str(active_assistant.id)
        assert response.data["results"][0]["requests"] == 1

    def test_by_api_key_groups_results(self, api_key_client, active_assistant, auth_client, api_key):
        chat(api_key_client, active_assistant)
        response = auth_client.get(BY_API_KEY_URL)
        assert response.status_code == 200
        assert response.data["results"][0]["api_key_id"] == str(api_key[0].id)
        assert response.data["results"][0]["requests"] == 1

    def test_invalid_date_range_rejected(self, auth_client):
        response = auth_client.get(SUMMARY_URL, {"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"})
        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_date_range"

    def test_requires_authentication(self, api_client):
        assert api_client.get(SUMMARY_URL).status_code == 401

    def test_api_key_without_usage_scope_is_forbidden(self, api_key_client):
        response = api_key_client.get(SUMMARY_URL)
        assert response.status_code == 403

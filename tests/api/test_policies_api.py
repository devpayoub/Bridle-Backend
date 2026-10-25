"""Policy configuration and independent evaluation API tests."""
from __future__ import annotations

import pytest

from apps.policies.models import PolicyEvaluation

POLICY_URL = "/api/v1/assistants/{}/policy/"
EVAL_REQUEST_URL = "/api/v1/policies/evaluate-request/"
EVAL_RESPONSE_URL = "/api/v1/policies/evaluate-response/"


@pytest.mark.django_db
class TestPolicyConfiguration:
    def test_get_policy(self, auth_client, assistant):
        response = auth_client.get(POLICY_URL.format(assistant.id))
        assert response.status_code == 200
        assert response.data["allowed_topics"] == assistant.allowed_topics
        assert response.data["version"] == 1
        assert response.data["evaluator_mode"] == "RULES"

    def test_put_updates_topics_and_bumps_versions(self, auth_client, assistant):
        payload = {
            "allowed_topics": ["check-in times", "spa bookings|spa"],
            "prohibited_topics": ["medical advice"],
            "escalation_rules": {"keywords": ["complaint", "manager"]},
            "response_constraints": {"max_chars": 400, "block_pii": True},
            "uncertainty_handling": "REFUSE",
            "allow_threshold": 0.7,
            "clarify_threshold": 0.4,
        }
        response = auth_client.put(POLICY_URL.format(assistant.id), payload, format="json")
        assert response.status_code == 200
        assert response.data["version"] == 2
        assert response.data["allowed_topics"] == ["check-in times", "spa bookings|spa"]
        assert response.data["escalation_rules"]["keywords"] == ["complaint", "manager"]

        assistant.refresh_from_db()
        assert assistant.allowed_topics == ["check-in times", "spa bookings|spa"]
        assert assistant.config_version == 2

    def test_invalid_thresholds_rejected(self, auth_client, assistant):
        response = auth_client.put(
            POLICY_URL.format(assistant.id),
            {"allow_threshold": 0.3, "clarify_threshold": 0.8},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_constraint_key_rejected(self, auth_client, assistant):
        response = auth_client.put(
            POLICY_URL.format(assistant.id),
            {"response_constraints": {"disable_platform_controls": True}},
            format="json",
        )
        assert response.status_code == 400

    def test_read_only_cannot_update_policy(self, organization, assistant, membership_factory):
        from rest_framework.test import APIClient

        _, read_only = membership_factory(organization, role="READ_ONLY")
        client = APIClient()
        client.force_authenticate(user=read_only)
        client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        response = client.put(POLICY_URL.format(assistant.id), {"industry": "hotel"}, format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestIndependentEvaluation:
    def test_inline_policy_allows_in_domain(self, auth_client):
        payload = {
            "policy": {"allowed_topics": ["check-in times", "room service"]},
            "request_text": "What time is check-in?",
        }
        response = auth_client.post(EVAL_REQUEST_URL, payload, format="json")
        assert response.status_code == 200
        assert response.data["decision"] == "ALLOW"
        assert response.data["reason_code"] == "ALLOWED"
        assert response.data["metadata"]["matched_topic"]
        assert PolicyEvaluation.objects.filter(phase="REQUEST").exists()

    def test_inline_policy_refuses_out_of_domain(self, auth_client):
        payload = {
            "policy": {"allowed_topics": ["check-in times"]},
            "request_text": "Who won the World Cup final?",
        }
        response = auth_client.post(EVAL_REQUEST_URL, payload, format="json")
        assert response.status_code == 200
        assert response.data["decision"] == "REFUSE"
        assert response.data["reason_code"] == "OUT_OF_DOMAIN"

    def test_evaluation_with_assistant_policy(self, auth_client, assistant):
        payload = {
            "assistant_id": str(assistant.id),
            "request_text": "Can I bring my dog?",
            "proposed_response": "Yes, pets are welcome for 25 USD per night.",
        }
        response = auth_client.post(EVAL_REQUEST_URL, payload, format="json")
        assert response.status_code == 200
        assert response.data["decision"] == "ALLOW"
        assert "response_evaluation" in response.data
        assert response.data["response_evaluation"]["decision"] == "ALLOW"

    def test_requires_policy_or_assistant(self, auth_client):
        response = auth_client.post(
            EVAL_REQUEST_URL, {"request_text": "hello"}, format="json"
        )
        assert response.status_code == 400

    def test_cross_tenant_assistant_is_404(self, auth_client, other_organization, assistant_payload):
        from apps.assistants.services import create_assistant

        foreign = create_assistant(organization=other_organization, data=assistant_payload(name="Foreign"))
        response = auth_client.post(
            EVAL_REQUEST_URL,
            {"assistant_id": str(foreign.id), "request_text": "hello"},
            format="json",
        )
        assert response.status_code == 404

    def test_response_evaluation_blocks_banned_phrase(self, auth_client, assistant):
        payload = {
            "assistant_id": str(assistant.id),
            "request_text": "What time is check-in?",
            "proposed_response": "We guarantee a refund for everything.",
            "policy": {"response_constraints": {"banned_phrases": ["guarantee a refund"]}},
        }
        # Inline policy is ignored when assistant_id is supplied; configure the
        # assistant policy instead.
        auth_client.put(
            POLICY_URL.format(assistant.id),
            {"response_constraints": {"banned_phrases": ["guarantee a refund"]}},
            format="json",
        )
        payload.pop("policy")
        response = auth_client.post(EVAL_RESPONSE_URL, payload, format="json")
        assert response.status_code == 200
        assert response.data["decision"] == "REFUSE"
        assert response.data["reason_code"] == "RESPONSE_POLICY_VIOLATION"

    def test_response_endpoint_requires_response(self, auth_client):
        response = auth_client.post(
            EVAL_RESPONSE_URL,
            {"policy": {"allowed_topics": ["check-in"]}, "request_text": "hi"},
            format="json",
        )
        assert response.status_code == 400

    def test_evaluation_consumes_usage(self, auth_client, organization):
        from apps.usage.models import UsageRecord

        auth_client.post(
            EVAL_REQUEST_URL,
            {"policy": {"allowed_topics": ["check-in"]}, "request_text": "check-in time?"},
            format="json",
        )
        assert UsageRecord.objects.filter(
            organization=organization, kind=UsageRecord.Kind.POLICY_EVALUATION
        ).count() == 1

    def test_quota_exhausted_returns_429(self, auth_client, organization, set_limit):
        set_limit(organization, "policy_evaluations", 0)
        response = auth_client.post(
            EVAL_REQUEST_URL,
            {"policy": {"allowed_topics": ["check-in"]}, "request_text": "check-in time?"},
            format="json",
        )
        assert response.status_code == 429
        assert response.data["error"]["code"] == "quota_exceeded"

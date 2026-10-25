"""Assistant management API tests."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.assistants.models import Assistant, AssistantConfigurationVersion
from apps.policies.models import AssistantPolicy

ASSISTANTS_URL = "/api/v1/assistants/"


def detail_url(assistant_id, suffix: str = "") -> str:
    return f"{ASSISTANTS_URL}{assistant_id}/{suffix}"


@pytest.mark.django_db
class TestAssistantCrud:
    def test_create_assistant(self, auth_client, organization, assistant_payload):
        response = auth_client.post(ASSISTANTS_URL, assistant_payload(), format="json")
        assert response.status_code == 201
        assert response.data["status"] == "DRAFT"
        assert response.data["config_version"] == 1
        assert response.data["policy_version"] == 1
        assert AssistantConfigurationVersion.objects.filter(
            assistant_id=response.data["id"], version=1
        ).exists()
        assert AssistantPolicy.objects.filter(assistant_id=response.data["id"]).exists()

    def test_plan_limit_enforced(self, auth_client, assistant, assistant_payload):
        response = auth_client.post(
            ASSISTANTS_URL, assistant_payload(name="Second Assistant"), format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "entitlement_exceeded"

    def test_list_is_tenant_scoped(self, auth_client, assistant, other_organization, assistant_payload):
        from apps.assistants.services import create_assistant

        create_assistant(organization=other_organization, data=assistant_payload(name="Other"))
        response = auth_client.get(ASSISTANTS_URL)
        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == str(assistant.id)

    def test_retrieve(self, auth_client, assistant):
        response = auth_client.get(detail_url(assistant.id))
        assert response.status_code == 200
        assert response.data["name"] == assistant.name

    def test_cross_tenant_retrieve_is_404(self, auth_client, other_organization, assistant_payload):
        from apps.assistants.services import create_assistant

        foreign = create_assistant(organization=other_organization, data=assistant_payload(name="Foreign"))
        response = auth_client.get(detail_url(foreign.id))
        assert response.status_code == 404

    def test_patch_creates_new_configuration_version(self, auth_client, assistant):
        response = auth_client.patch(
            detail_url(assistant.id), {"description": "Updated description"}, format="json"
        )
        assert response.status_code == 200
        assert response.data["config_version"] == 2
        assert AssistantConfigurationVersion.objects.filter(assistant=assistant).count() == 2

    def test_invalid_topic_overlap_rejected(self, auth_client, assistant):
        response = auth_client.patch(
            detail_url(assistant.id),
            {"allowed_topics": ["pets"], "prohibited_topics": ["pets"]},
            format="json",
        )
        assert response.status_code == 400

    def test_unsupported_generation_parameter_rejected(self, auth_client, assistant):
        response = auth_client.patch(
            detail_url(assistant.id),
            {"generation_parameters": {"top_k": 5}},
            format="json",
        )
        assert response.status_code == 400

    def test_read_only_cannot_patch(self, organization, assistant, membership_factory):
        _, read_only = membership_factory(organization, role="READ_ONLY")
        client = APIClient()
        client.force_authenticate(user=read_only)
        client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        response = client.patch(detail_url(assistant.id), {"name": "Hacked"}, format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestLifecycle:
    def test_activate_requires_configuration(self, auth_client, assistant):
        assistant.allowed_topics = []
        assistant.save(update_fields=["allowed_topics"])
        response = auth_client.post(detail_url(assistant.id, "activate/"))
        assert response.status_code == 400
        assert response.data["error"]["code"] == "assistant_not_ready"
        assert response.data["error"]["details"]["issues"]

    def test_activate_pause_archive(self, auth_client, assistant):
        assert auth_client.post(detail_url(assistant.id, "activate/")).data["status"] == "ACTIVE"
        assert auth_client.post(detail_url(assistant.id, "pause/")).data["status"] == "PAUSED"
        assert auth_client.post(detail_url(assistant.id, "activate/")).data["status"] == "ACTIVE"
        assert auth_client.post(detail_url(assistant.id, "archive/")).data["status"] == "ARCHIVED"

    def test_archived_assistant_cannot_be_edited(self, auth_client, assistant):
        auth_client.post(detail_url(assistant.id, "archive/"))
        response = auth_client.patch(detail_url(assistant.id), {"name": "Nope"}, format="json")
        assert response.status_code == 400
        assert response.data["error"]["code"] == "assistant_archived"

    def test_invalid_transition_rejected(self, auth_client, assistant):
        auth_client.post(detail_url(assistant.id, "archive/"))
        response = auth_client.post(detail_url(assistant.id, "activate/"))
        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_status_transition"

    def test_duplicate_copies_configuration(self, auth_client, assistant, set_limit, organization):
        set_limit(organization, "assistants", 5)
        response = auth_client.post(detail_url(assistant.id, "duplicate/"), {}, format="json")
        assert response.status_code == 201
        assert response.data["status"] == "DRAFT"
        assert response.data["id"] != str(assistant.id)
        assert response.data["allowed_topics"] == assistant.allowed_topics

    def test_usage_statistics(self, auth_client, assistant):
        response = auth_client.get(detail_url(assistant.id, "usage/"))
        assert response.status_code == 200
        assert response.data["total_requests"] == 0


@pytest.mark.django_db
class TestEvaluationEndpoint:
    def test_evaluation_reports_false_acceptance(self, auth_client, assistant):
        payload = {
            "cases": [
                {"input": "What time is check-in?", "expected_decision": "ALLOW"},
                {"input": "Who won the World Cup?", "expected_decision": "REFUSE"},
                {"input": "Do you allow pets?", "expected_decision": "REFUSE"},
            ]
        }
        response = auth_client.post(detail_url(assistant.id, "evaluations/"), payload, format="json")
        assert response.status_code == 200
        summary = response.data["summary"]
        assert summary["total"] == 3
        assert summary["correct"] == 2
        assert summary["false_acceptance"] == 1
        assert "disclaimer" in response.data

    def test_empty_case_list_rejected(self, auth_client, assistant):
        response = auth_client.post(detail_url(assistant.id, "evaluations/"), {"cases": []}, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
def test_assistant_defaults_to_configured_provider(auth_client, assistant_payload):
    payload = assistant_payload()
    payload.pop("model_provider")
    payload.pop("model_name")
    response = auth_client.post(ASSISTANTS_URL, payload, format="json")
    assert response.status_code == 201
    assert response.data["model_provider"] == "mock"
    assert response.data["model_name"] == "mock-model-1"


@pytest.mark.django_db
def test_knowledge_base_ids_must_belong_to_tenant(auth_client, assistant_payload, other_organization):
    from apps.knowledge.services import create_knowledge_base

    foreign_kb = create_knowledge_base(organization=other_organization, name="Foreign KB")
    payload = assistant_payload()
    payload["knowledge_base_ids"] = [str(foreign_kb.id)]
    response = auth_client.post(ASSISTANTS_URL, payload, format="json")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_knowledge_base"

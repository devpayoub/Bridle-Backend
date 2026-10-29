"""System endpoints: health, readiness, OpenAPI documentation."""
from __future__ import annotations

import pytest

HEALTH_URL = "/api/v1/health/"
READY_URL = "/api/v1/ready/"
SCHEMA_URL = "/api/schema/"
DOCS_URL = "/api/docs/"


@pytest.mark.django_db
class TestOperationalEndpoints:
    def test_health_is_public_and_minimal(self, api_client):
        response = api_client.get(HEALTH_URL)
        assert response.status_code == 200
        assert response.data == {"status": "ok", "service": "assistant-platform-api"}

    def test_readiness_checks_dependencies(self, api_client):
        response = api_client.get(READY_URL)
        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert response.data["checks"]["database"] == "ok"
        assert response.data["checks"]["cache"] == "ok"
        # Reachability only: an unauthenticated probe must not advertise features.
        assert "vector_search" not in response.data

    def test_health_does_not_leak_internals(self, api_client):
        body = str(api_client.get(READY_URL).data)
        assert "postgres://" not in body
        assert "redis://" not in body
        assert "SECRET" not in body

    def test_request_id_header_is_returned(self, api_client):
        response = api_client.get(HEALTH_URL)
        assert response["X-Request-ID"]

    def test_request_id_is_echoed_when_valid(self, api_client):
        response = api_client.get(HEALTH_URL, HTTP_X_REQUEST_ID="abc12345-abc12345")
        assert response["X-Request-ID"] == "abc12345-abc12345"


@pytest.mark.django_db
class TestApiDocumentation:
    def test_schema_generates(self, api_client):
        response = api_client.get(SCHEMA_URL)
        assert response.status_code == 200
        content = response.content.decode()
        assert "/api/v1/chat/completions/" in content
        assert "/api/v1/assistants/" in content
        assert "/api/v1/policies/evaluate-request/" in content
        assert "/api/v1/knowledge-bases/" in content
        assert "/api/v1/usage/summary/" in content

    def test_swagger_ui_is_available_in_development(self, api_client):
        assert api_client.get(DOCS_URL).status_code == 200

    def test_error_envelope_for_unknown_route(self, api_client):
        response = api_client.get("/api/v1/does-not-exist/")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"


@pytest.mark.django_db
def test_validation_error_envelope(auth_client):
    response = auth_client.post("/api/v1/assistants/", {"name": ""}, format="json")
    assert response.status_code == 400
    body = response.data["error"]
    assert body["code"] == "validation_error"
    assert body["request_id"]

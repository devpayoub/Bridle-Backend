"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.ai_gateway.registry import reset_providers
from apps.assistants.models import Assistant
from apps.assistants.services import change_status, create_assistant
from apps.knowledge.models import Document
from apps.knowledge.services import create_document, create_knowledge_base
from apps.organizations.models import OrganizationMembership
from apps.organizations.services import add_member, create_organization
from tests.factories import TEST_PASSWORD, AssistantFactory, UserFactory

HOTEL_HANDBOOK = """
Guest Handbook

Check-in time is 3:00 PM and check-out time is 11:00 AM.
Early check-in is available from 12:00 PM subject to room availability.

Room service is available daily from 7:00 AM until 11:00 PM.
The room service menu includes breakfast, lunch and dinner.

Reservations can be changed up to 48 hours before arrival without a fee.
Cancellations within 24 hours of arrival are charged one night.

Pets are welcome in designated rooms for a fee of 25 USD per night.
Guide dogs stay free of charge.
"""


@pytest.fixture(autouse=True)
def _reset_provider_registry():
    reset_providers()
    yield
    reset_providers()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Rate-limit and quota counters live in the cache; isolate every test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return settings.MEDIA_ROOT


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory(email="owner@example.com", full_name="Owner")


@pytest.fixture
def other_user(db):
    return UserFactory(email="outsider@example.com", full_name="Outsider")


@pytest.fixture
def organization(db, user):
    return create_organization(owner=user, name="Acme Hotel")


@pytest.fixture
def other_organization(db, other_user):
    return create_organization(owner=other_user, name="Other Hotel")


@pytest.fixture
def auth_client(api_client, user, organization) -> APIClient:
    api_client.force_authenticate(user=user)
    api_client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
    return api_client


@pytest.fixture
def other_auth_client(api_client, other_user, other_organization) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=other_user)
    client.credentials(HTTP_X_ORGANIZATION_ID=str(other_organization.id))
    return client


def _assistant_payload(**overrides) -> dict:
    payload = {
        "name": "Hotel Concierge",
        "description": "Answers guest questions about the hotel.",
        "domain_type": Assistant.DomainType.HOTEL,
        "allowed_topics": [
            "check-in times|checkin|arrival time",
            "room service",
            "reservations|booking",
            "pet policy|pets|dogs|cats",
        ],
        "prohibited_topics": ["medical advice", "legal advice"],
        "system_instructions": "Be concise. Use only approved hotel information.",
        "fallback_message": "I can only help with questions about our hotel.",
        "model_provider": "mock",
        "model_name": "mock-model-1",
        "maximum_output_tokens": 300,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def assistant_payload():
    return _assistant_payload


@pytest.fixture
def assistant(db, organization) -> Assistant:
    return create_assistant(organization=organization, data=_assistant_payload(), actor=None)


@pytest.fixture
def active_assistant(assistant) -> Assistant:
    return change_status(assistant, Assistant.Status.ACTIVE)


@pytest.fixture
def knowledge_base(db, organization):
    return create_knowledge_base(organization=organization, name="Hotel Handbook")


@pytest.fixture
def ready_document(db, organization, knowledge_base) -> Document:
    """Uploaded and ingested synchronously so retrieval is deterministic."""
    from apps.knowledge.ingestion import ingest_document

    upload = SimpleUploadedFile("handbook.txt", HOTEL_HANDBOOK.encode("utf-8"), content_type="text/plain")
    document = create_document(
        organization=organization, knowledge_base=knowledge_base, upload=upload, title="Guest Handbook"
    )
    ingest_document(str(document.id))
    document.refresh_from_db()
    return document


@pytest.fixture
def grounded_assistant(active_assistant, knowledge_base, ready_document) -> Assistant:
    active_assistant.knowledge_bases.add(knowledge_base)
    active_assistant.knowledge_required = True
    active_assistant.save(update_fields=["knowledge_required", "updated_at"])
    return active_assistant


@pytest.fixture
def api_key(organization, user):
    """Return ``(record, plaintext)`` for a chat-capable key."""
    from apps.api_keys.services import create_api_key

    return create_api_key(
        organization=organization,
        name="Test key",
        environment="DEVELOPMENT",
        scopes=["chat:write", "assistant:read"],
        created_by=user,
    )


@pytest.fixture
def api_key_client(api_key) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {api_key[1]}")
    return client


@pytest.fixture
def membership_factory(db):
    def _make(organization, role=OrganizationMembership.Role.MEMBER, email=None):
        new_user = UserFactory(email=email) if email else UserFactory()
        return add_member(organization=organization, email=new_user.email, role=role), new_user

    return _make


@pytest.fixture
def set_limit(db):
    """Override a plan limit for one organization (mirrors a negotiated cap)."""
    from apps.billing.models import UsageLimit

    def _apply(organization, metric: str, value: int):
        UsageLimit.objects.update_or_create(
            organization=organization, metric=metric, defaults={"limit_value": value}
        )

    return _apply

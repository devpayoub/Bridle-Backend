"""Unit tests for API key generation, hashing and lifecycle."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from apps.api_keys.models import APIKey
from apps.api_keys.services import authenticate_api_key, create_api_key, revoke_api_key, rotate_api_key
from apps.common.crypto import hmac_digest


@pytest.fixture
def created_key(organization, user):
    return create_api_key(
        organization=organization,
        name="Integration key",
        environment=APIKey.Environment.PRODUCTION,
        scopes=["chat:write"],
        created_by=user,
    )


def test_key_format_and_prefix(created_key):
    record, plaintext = created_key
    # Current format: "bb-" + 47 URL-safe characters, no separator segment.
    assert plaintext.startswith("bb-")
    assert len(plaintext) == 50
    assert plaintext.startswith(record.prefix)
    assert record.prefix.startswith("bb-")


def test_plaintext_is_not_stored(created_key):
    record, plaintext = created_key
    stored = APIKey.objects.get(pk=record.pk)
    assert plaintext not in stored.key_hash
    assert stored.key_hash == hmac_digest(plaintext)


def test_authenticate_valid_key(created_key):
    record, plaintext = created_key
    resolved = authenticate_api_key(plaintext)
    assert resolved.pk == record.pk
    assert resolved.last_used_at is not None


def test_authenticate_wrong_secret(created_key):
    record, plaintext = created_key
    prefix, _, _ = plaintext.partition(".")
    with pytest.raises(AuthenticationFailed):
        authenticate_api_key(f"{prefix}.wrong-secret")


def test_authenticate_unknown_prefix(db):
    with pytest.raises(AuthenticationFailed):
        authenticate_api_key("bb-doesnotexist")


def test_authenticate_malformed_key():
    with pytest.raises(AuthenticationFailed):
        authenticate_api_key("not-a-key")


def test_expired_key_rejected(created_key):
    record, plaintext = created_key
    APIKey.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(AuthenticationFailed):
        authenticate_api_key(plaintext)


def test_revoked_key_rejected(created_key):
    record, plaintext = created_key
    revoke_api_key(record)
    with pytest.raises(AuthenticationFailed):
        authenticate_api_key(plaintext)


def test_revocation_is_idempotent(created_key):
    record, _ = created_key
    revoke_api_key(record)
    first = APIKey.objects.get(pk=record.pk).revoked_at
    revoke_api_key(APIKey.objects.get(pk=record.pk))
    assert APIKey.objects.get(pk=record.pk).revoked_at == first


def test_rotation_revokes_old_and_issues_new(created_key):
    record, plaintext = created_key
    new_record, new_plaintext = rotate_api_key(record)

    assert new_plaintext != plaintext
    assert new_record.pk != record.pk
    assert new_record.rotated_from_id == record.pk
    assert new_record.scopes == record.scopes

    with pytest.raises(AuthenticationFailed):
        authenticate_api_key(plaintext)
    assert authenticate_api_key(new_plaintext).pk == new_record.pk


def test_cannot_rotate_revoked_key(created_key):
    record, _ = created_key
    revoke_api_key(record)
    from apps.common.exceptions import APIError

    with pytest.raises(APIError):
        rotate_api_key(APIKey.objects.get(pk=record.pk))


def test_scoped_key_knows_its_scope(created_key):
    record, _ = created_key
    assert record.has_scope("chat:write")
    assert not record.has_scope("assistant:write")
    assert not record.has_scope("*")


def test_wildcard_scope_grants_everything(organization, user):
    record, _ = create_api_key(
        organization=organization, name="Admin key", scopes=["*"], created_by=user
    )
    assert record.has_scope("assistant:write")

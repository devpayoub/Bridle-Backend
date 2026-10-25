"""Authentication and account lifecycle API tests."""
from __future__ import annotations

import re

import pytest
from django.core import mail

from apps.accounts.models import User

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
ME_URL = "/api/v1/auth/me/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
VERIFY_URL = "/api/v1/auth/verify-email/"
RESET_URL = "/api/v1/auth/password-reset/"
RESET_CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
CHANGE_PASSWORD_URL = "/api/v1/auth/password-change/"

PASSWORD = "Str0ng-Passw0rd!2026"


def _token_from_last_email() -> str:
    body = mail.outbox[-1].body
    match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert match, f"No token found in email body: {body}"
    return match.group(1)


@pytest.mark.django_db
class TestRegistration:
    def test_register_creates_user_and_sends_verification(self, api_client):
        response = api_client.post(
            REGISTER_URL,
            {"email": "new@example.com", "password": PASSWORD, "full_name": "New User"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["user"]["email"] == "new@example.com"
        assert response.data["user"]["is_email_verified"] is False
        assert User.objects.filter(email="new@example.com").exists()
        assert len(mail.outbox) == 1
        assert "verify-email" in mail.outbox[0].body

    def test_password_is_hashed(self, api_client):
        api_client.post(
            REGISTER_URL, {"email": "hash@example.com", "password": PASSWORD}, format="json"
        )
        user = User.objects.get(email="hash@example.com")
        assert user.password != PASSWORD
        assert user.check_password(PASSWORD)

    def test_duplicate_email_rejected(self, api_client):
        api_client.post(
            REGISTER_URL, {"email": "dupe@example.com", "password": PASSWORD}, format="json"
        )
        response = api_client.post(
            REGISTER_URL, {"email": "dupe@example.com", "password": PASSWORD}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "email_already_registered"

    def test_weak_password_rejected(self, api_client):
        response = api_client.post(
            REGISTER_URL, {"email": "weak@example.com", "password": "password"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert not User.objects.filter(email="weak@example.com").exists()

    def test_email_is_normalised(self, api_client):
        api_client.post(
            REGISTER_URL, {"email": "MiXeD@Example.COM", "password": PASSWORD}, format="json"
        )
        assert User.objects.filter(email="mixed@example.com").exists()


@pytest.mark.django_db
class TestLogin:
    def test_login_returns_tokens(self, api_client, user):
        response = api_client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        assert response.status_code == 200
        assert response.data["access"]
        assert response.data["refresh"]
        assert response.data["user"]["email"] == user.email

    def test_wrong_password_returns_401(self, api_client, user):
        response = api_client.post(LOGIN_URL, {"email": user.email, "password": "wrong"}, format="json")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "authentication_failed"

    def test_unknown_user_returns_401(self, api_client, db):
        response = api_client.post(
            LOGIN_URL, {"email": "nobody@example.com", "password": PASSWORD}, format="json"
        )
        assert response.status_code == 401

    def test_inactive_user_cannot_login(self, api_client, user):
        user.is_active = False
        user.save(update_fields=["is_active"])
        response = api_client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        assert response.status_code == 401

    def test_me_requires_authentication(self, api_client, db):
        assert api_client.get(ME_URL).status_code == 401

    def test_me_returns_memberships(self, auth_client, user, organization):
        response = auth_client.get(ME_URL)
        assert response.status_code == 200
        assert response.data["user"]["email"] == user.email
        assert response.data["memberships"][0]["organization_id"] == str(organization.id)
        assert response.data["memberships"][0]["role"] == "OWNER"

    def test_refresh_token_rotates(self, api_client, user):
        login = api_client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        response = api_client.post(
            REFRESH_URL, {"refresh": login.data["refresh"]}, format="json"
        )
        assert response.status_code == 200
        assert response.data["access"]

    def test_logout_blacklists_refresh_token(self, api_client, user):
        login = api_client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        logout = api_client.post(LOGOUT_URL, {"refresh": login.data["refresh"]}, format="json")
        assert logout.status_code == 204

        api_client.credentials()
        retry = api_client.post(REFRESH_URL, {"refresh": login.data["refresh"]}, format="json")
        assert retry.status_code == 401


@pytest.mark.django_db
class TestEmailVerification:
    def test_verify_email_flow(self, api_client):
        api_client.post(
            REGISTER_URL, {"email": "verify@example.com", "password": PASSWORD}, format="json"
        )
        token = _token_from_last_email()
        response = api_client.post(VERIFY_URL, {"token": token}, format="json")
        assert response.status_code == 200
        assert response.data["user"]["is_email_verified"] is True

    def test_token_cannot_be_reused(self, api_client):
        api_client.post(
            REGISTER_URL, {"email": "reuse@example.com", "password": PASSWORD}, format="json"
        )
        token = _token_from_last_email()
        api_client.post(VERIFY_URL, {"token": token}, format="json")
        second = api_client.post(VERIFY_URL, {"token": token}, format="json")
        assert second.status_code == 400
        assert second.data["error"]["code"] == "invalid_token"

    def test_invalid_token_rejected(self, api_client, db):
        response = api_client.post(VERIFY_URL, {"token": "not-a-real-token"}, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestPasswordReset:
    def test_reset_flow(self, api_client, user):
        response = api_client.post(RESET_URL, {"email": user.email}, format="json")
        assert response.status_code == 202

        token = _token_from_last_email()
        confirm = api_client.post(
            RESET_CONFIRM_URL,
            {"token": token, "new_password": "N3w-Passw0rd!2026"},
            format="json",
        )
        assert confirm.status_code == 200

        user.refresh_from_db()
        assert user.check_password("N3w-Passw0rd!2026")

    def test_unknown_email_does_not_leak(self, api_client, db):
        response = api_client.post(RESET_URL, {"email": "ghost@example.com"}, format="json")
        assert response.status_code == 202
        assert len(mail.outbox) == 0

    def test_change_password_requires_current(self, auth_client, user):
        response = auth_client.post(
            CHANGE_PASSWORD_URL,
            {"current_password": "wrong", "new_password": "N3w-Passw0rd!2026"},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_password"


@pytest.mark.django_db
class TestAuthRateLimiting:
    def test_login_rate_limit_returns_429(self, api_client, user, settings):
        settings.RATE_LIMITS = {**settings.RATE_LIMITS, "auth.login": {"limit": 2, "window": 60}}
        payload = {"email": user.email, "password": "wrong"}

        assert api_client.post(LOGIN_URL, payload, format="json").status_code == 401
        assert api_client.post(LOGIN_URL, payload, format="json").status_code == 401
        response = api_client.post(LOGIN_URL, payload, format="json")
        assert response.status_code == 429
        assert response.data["error"]["code"] == "rate_limited"
        assert "Retry-After" in response

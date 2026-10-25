"""Account domain services.

Business rules live here, not in views or serializers, so they can be reused by
management commands, Celery tasks and tests.
"""
from __future__ import annotations

import logging

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.emails import send_password_reset_email, send_verification_email
from apps.accounts.models import OneTimeToken, User
from apps.common.exceptions import APIError
from apps.common.logging import set_log_context

logger = logging.getLogger("apps.accounts")

VERIFICATION_TOKEN_TTL = 60 * 60 * 24
PASSWORD_RESET_TOKEN_TTL = 60 * 60


def validate_password_strength(password: str, user: User | None = None) -> None:
    """Run Django's configured validators and surface errors as field errors."""
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": list(exc.messages)}) from exc


def blacklist_refresh_tokens(user: User) -> None:
    """Invalidate every outstanding refresh token for a user.

    Access tokens are short lived (15 minutes by default) and cannot be revoked
    without server-side state; this is documented as a known limitation.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    except ImportError:  # pragma: no cover - optional dependency guard
        return

    tokens = OutstandingToken.objects.filter(user=user)
    for token in tokens.iterator():
        BlacklistedToken.objects.get_or_create(token=token)


@transaction.atomic
def register_user(*, email: str, password: str, full_name: str = "") -> tuple[User, str]:
    """Create a user and issue an email-verification token.

    Returns ``(user, raw_token)``; the caller is responsible for delivery.
    """
    normalised = User.objects.normalize_email(email).strip().lower()
    if User.objects.filter(email=normalised).exists():
        raise APIError(
            "An account with this email address already exists.",
            code="email_already_registered",
        )

    validate_password_strength(password)
    user = User.objects.create_user(email=normalised, password=password, full_name=full_name)
    _, raw_token = OneTimeToken.issue(
        user=user, purpose=OneTimeToken.Purpose.EMAIL_VERIFICATION, ttl_seconds=VERIFICATION_TOKEN_TTL
    )
    return user, raw_token


def send_registration_email(user: User, raw_token: str) -> None:
    send_verification_email(email=user.email, token=raw_token)


def resend_verification(user: User) -> None:
    if user.is_email_verified:
        raise APIError("This email address is already verified.", code="already_verified")
    OneTimeToken.objects.filter(
        user=user,
        purpose=OneTimeToken.Purpose.EMAIL_VERIFICATION,
        used_at__isnull=True,
    ).update(used_at=timezone.now())
    _, raw_token = OneTimeToken.issue(
        user=user, purpose=OneTimeToken.Purpose.EMAIL_VERIFICATION, ttl_seconds=VERIFICATION_TOKEN_TTL
    )
    send_verification_email(email=user.email, token=raw_token)


def _find_token(raw_token: str, purpose: str) -> OneTimeToken:
    from apps.common.crypto import hmac_digest

    token = (
        OneTimeToken.objects.select_related("user")
        .filter(token_hash=hmac_digest(raw_token), purpose=purpose)
        .first()
    )
    if token is None or not token.is_valid:
        raise APIError("This link is invalid or has expired.", code="invalid_token")
    return token


@transaction.atomic
def verify_email(raw_token: str) -> User:
    token = _find_token(raw_token, OneTimeToken.Purpose.EMAIL_VERIFICATION)
    token.mark_used()
    token.user.mark_email_verified()
    return token.user


def request_password_reset(email: str) -> None:
    """Issue a reset link. Never reveals whether the address is registered."""
    user = User.objects.filter(email=email.strip().lower(), is_active=True).first()
    if user is None:
        logger.info("Password reset requested for unknown address")
        return

    OneTimeToken.objects.filter(
        user=user, purpose=OneTimeToken.Purpose.PASSWORD_RESET, used_at__isnull=True
    ).update(used_at=timezone.now())
    _, raw_token = OneTimeToken.issue(
        user=user, purpose=OneTimeToken.Purpose.PASSWORD_RESET, ttl_seconds=PASSWORD_RESET_TOKEN_TTL
    )
    send_password_reset_email(email=user.email, token=raw_token)


@transaction.atomic
def confirm_password_reset(raw_token: str, new_password: str) -> User:
    token = _find_token(raw_token, OneTimeToken.Purpose.PASSWORD_RESET)
    user = token.user
    validate_password_strength(new_password, user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    token.mark_used()
    OneTimeToken.objects.filter(
        user=user, purpose=OneTimeToken.Purpose.PASSWORD_RESET, used_at__isnull=True
    ).update(used_at=timezone.now())
    blacklist_refresh_tokens(user)
    set_log_context(user_id=user.id)
    logger.info("Password reset completed")
    return user


@transaction.atomic
def change_password(user: User, current_password: str, new_password: str) -> None:
    if not user.check_password(current_password):
        raise APIError("The current password is incorrect.", code="invalid_password")
    validate_password_strength(new_password, user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    blacklist_refresh_tokens(user)
    logger.info("Password changed")


@transaction.atomic
def deactivate_account(user: User) -> None:
    user.deactivate()
    blacklist_refresh_tokens(user)
    logger.info("Account deactivated")

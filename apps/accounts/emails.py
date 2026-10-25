"""Transactional email delivery for account workflows."""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("apps.accounts.email")


def _send(subject: str, body: str, recipient: str) -> bool:
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        return True
    except Exception:
        # Never let a mail outage break an account operation; the user can
        # request a new link. The failure is logged without the token value.
        logger.exception("Unable to deliver account email to user")
        return False


def send_verification_email(*, email: str, token: str) -> bool:
    link = f"{settings.FRONTEND_BASE_URL}/verify-email?token={token}"
    body = (
        "Confirm your email address to finish setting up your account.\n\n"
        f"{link}\n\n"
        "This link expires in 24 hours. If you did not create an account you can ignore this email."
    )
    return _send("Verify your email address", body, email)


def send_password_reset_email(*, email: str, token: str) -> bool:
    link = f"{settings.FRONTEND_BASE_URL}/reset-password?token={token}"
    body = (
        "A password reset was requested for your account.\n\n"
        f"{link}\n\n"
        "This link expires in 1 hour and can be used once. "
        "If you did not request a reset, no action is required."
    )
    return _send("Reset your password", body, email)

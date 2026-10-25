"""User account and one-time token models."""
from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.common.crypto import hmac_digest, verify_digest
from apps.common.models import TimeStampedModel, UUIDModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _normalise(self, email: str) -> str:
        return self.normalize_email(email).strip().lower()

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self._normalise(email)
        extra_fields.setdefault("is_active", True)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError("Superusers must have is_staff=True and is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):
    """Email-first user. Passwords are always stored through Django hashers."""

    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        ordering = ("-date_joined",)

    def __str__(self) -> str:
        return self.email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    def mark_email_verified(self) -> None:
        if self.email_verified_at is None:
            self.email_verified_at = timezone.now()
            self.save(update_fields=["email_verified_at", "updated_at"])

    def deactivate(self) -> None:
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.save(update_fields=["is_active", "deactivated_at", "updated_at"])


class OneTimeToken(UUIDModel, TimeStampedModel):
    """Hashed, single-use token for email verification and password resets.

    Only the HMAC digest is persisted, so a database leak does not reveal usable
    tokens. Tokens are invalidated on use and on password change.
    """

    class Purpose(models.TextChoices):
        EMAIL_VERIFICATION = "EMAIL_VERIFICATION", "Email verification"
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_tokens")
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    token_hash = models.CharField(max_length=128, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_one_time_token"
        indexes = [models.Index(fields=["user", "purpose", "used_at"])]

    def __str__(self) -> str:
        return f"{self.purpose} token for {self.user_id}"

    # -- helpers ---------------------------------------------------------
    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def matches(self, raw_token: str) -> bool:
        return verify_digest(raw_token, self.token_hash)

    def mark_used(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at", "updated_at"])

    @classmethod
    def issue(cls, *, user: User, purpose: str, ttl_seconds: int) -> tuple["OneTimeToken", str]:
        """Create a token and return ``(instance, raw_token)``.

        The raw token is never stored; it must be delivered to the user
        immediately (email) and then forgotten.
        """
        import secrets

        raw = secrets.token_urlsafe(32)
        instance = cls.objects.create(
            user=user,
            purpose=purpose,
            token_hash=hmac_digest(raw),
            expires_at=timezone.now() + timezone.timedelta(seconds=ttl_seconds),
        )
        return instance, raw

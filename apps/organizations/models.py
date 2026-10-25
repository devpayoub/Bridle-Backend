"""Tenant models: Organization and OrganizationMembership."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Organization(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        ARCHIVED = "ARCHIVED", "Archived"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    timezone = models.CharField(max_length=64, default="UTC")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "organizations_organization"
        ordering = ("name",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="org_status_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "SUSPENDED", "ARCHIVED"]),
                name="org_status_valid",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def plan(self):
        """Current plan, resolved through the subscription (single source of truth)."""
        subscription = getattr(self, "subscription", None)
        return subscription.plan if subscription else None


class OrganizationMembership(UUIDModel, TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        ADMIN = "ADMIN", "Admin"
        DEVELOPER = "DEVELOPER", "Developer"
        MEMBER = "MEMBER", "Member"
        READ_ONLY = "READ_ONLY", "Read only"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_memberships",
    )

    class Meta:
        db_table = "organizations_membership"
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uniq_org_membership"),
            models.CheckConstraint(
                condition=models.Q(role__in=["OWNER", "ADMIN", "DEVELOPER", "MEMBER", "READ_ONLY"]),
                name="membership_role_valid",
            ),
        ]
        indexes = [models.Index(fields=["user", "role"], name="membership_user_role_idx")]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}:{self.role}"


class OrganizationAISettings(UUIDModel, TimeStampedModel):
    """Workspace-level AI provider configuration.

    The API key is stored encrypted (see ``apps.common.crypto.encrypt_secret``) and
    never returned by the API — only its last four characters are exposed so an
    operator can tell which key is installed. When no row exists (or it has no key)
    the platform falls back to the environment configuration.
    """

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="ai_settings"
    )
    provider = models.CharField(max_length=32, default="")
    model = models.CharField(max_length=100, blank=True)
    api_key_ciphertext = models.TextField(blank=True)
    api_key_last4 = models.CharField(max_length=8, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_ai_settings",
    )

    class Meta:
        db_table = "organizations_ai_settings"
        verbose_name_plural = "organization AI settings"

    def __str__(self) -> str:
        return f"{self.organization_id} · {self.provider or 'default'}"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key_ciphertext)

    def set_api_key(self, raw: str) -> None:
        from apps.common.crypto import encrypt_secret

        raw = (raw or "").strip()
        if not raw:
            self.api_key_ciphertext = ""
            self.api_key_last4 = ""
            return
        self.api_key_ciphertext = encrypt_secret(raw)
        self.api_key_last4 = raw[-4:]

    def get_api_key(self) -> str:
        """Plaintext key, or ``""`` when absent or undecryptable."""
        from apps.common.crypto import decrypt_secret

        try:
            return decrypt_secret(self.api_key_ciphertext)
        except ValueError:
            return ""

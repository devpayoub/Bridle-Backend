"""API key model.

Security model
--------------
* Keys are shown once, at creation time. Only a keyed HMAC digest is stored.
* A public ``prefix`` is used to locate the record without revealing the secret,
  so verification is a single indexed lookup plus a constant-time comparison.
* Keys are scoped to exactly one organization and optionally to one assistant.
* Revocation is a timestamp; expired and revoked keys never authenticate.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel


class APIKey(UUIDModel, TimeStampedModel):
    class Environment(models.TextChoices):
        DEVELOPMENT = "DEVELOPMENT", "Development"
        STAGING = "STAGING", "Staging"
        PRODUCTION = "PRODUCTION", "Production"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=32, unique=True, db_index=True)
    key_hash = models.CharField(max_length=128)
    environment = models.CharField(
        max_length=16, choices=Environment.choices, default=Environment.DEVELOPMENT
    )
    scopes = models.JSONField(default=list)
    assistant = models.ForeignKey(
        "assistants.Assistant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="api_keys",
        help_text="When set, the key may only be used with this assistant.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_api_keys"
    )
    rotated_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="rotations"
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_keys_api_key"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organization", "revoked_at"], name="apikey_org_revoked_idx"),
            models.Index(fields=["organization", "environment"], name="apikey_org_env_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"

    # -- state -----------------------------------------------------------
    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_active(self) -> bool:
        return not self.is_revoked and not self.is_expired

    def has_scope(self, scope: str) -> bool:
        return "*" in (self.scopes or []) or scope in (self.scopes or [])

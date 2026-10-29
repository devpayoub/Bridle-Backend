"""Channel models.

Four tables, each with one job:

- ``Channel``       what a workspace connected (bot identity, encrypted token, webhook keys)
- ``ChannelIdentity`` one external user, for per-user limits and blocking
- ``ChannelThread`` an external chat mapped to a ``Conversation`` (survives retention)
- ``ChannelEvent``  the inbound ledger: dedupe on ``update_id`` + delivery state

Routing never uses ``Conversation.external_user_id``: retention blanks it and
``_load_conversation`` refuses anonymised rows, so it cannot be a stable key.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.crypto import decrypt_secret, encrypt_secret, hmac_digest, verify_digest
from apps.common.models import TimeStampedModel, UUIDModel


class Channel(UUIDModel, TimeStampedModel):
    """A messaging channel connected to one assistant."""

    class Type(models.TextChoices):
        TELEGRAM = "TELEGRAM", "Telegram"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        ERROR = "ERROR", "Error"
        DISABLED = "DISABLED", "Disabled"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="channels"
    )
    # PROTECT: a connected bot must be disconnected before its assistant disappears.
    assistant = models.ForeignKey(
        "assistants.Assistant", on_delete=models.PROTECT, related_name="channels"
    )
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.TELEGRAM, db_index=True)
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)

    # Identity as reported by the provider (Telegram bot id / @username).
    external_id = models.CharField(max_length=64, blank=True)
    external_username = models.CharField(max_length=64, blank=True)

    # Bot token: reversible (we must replay it to the provider on every send).
    credentials_ciphertext = models.TextField(blank=True)
    credentials_last4 = models.CharField(max_length=8, blank=True)

    # Webhook path key: reversible (the console displays the URL for copying).
    webhook_key_ciphertext = models.TextField(blank=True)
    # Webhook header secret: verify-only, so a digest is enough and safer.
    webhook_secret_digest = models.CharField(max_length=64, blank=True)

    webhook_registered_at = models.DateTimeField(null=True, blank=True)
    webhook_error = models.CharField(max_length=200, blank=True)

    last_event_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_channels",
    )

    class Meta:
        db_table = "channels_channel"
        ordering = ("-created_at",)
        constraints = [
            # One live bot cannot be connected twice, even across workspaces.
            models.UniqueConstraint(
                fields=["type", "external_id"],
                condition=models.Q(status="ACTIVE"),
                name="uniq_channel_bot_active",
            ),
            # One live channel per assistant per type.
            models.UniqueConstraint(
                fields=["assistant", "type"],
                condition=models.Q(status="ACTIVE"),
                name="uniq_active_channel_per_assistant",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["PENDING", "ACTIVE", "ERROR", "DISABLED"]),
                name="channel_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="channel_org_status_idx"),
            models.Index(fields=["organization", "-created_at"], name="channel_org_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_type_display()})"

    # -- bot token ---------------------------------------------------------
    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials_ciphertext)

    def set_credentials(self, raw: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            self.credentials_ciphertext = ""
            self.credentials_last4 = ""
            return
        self.credentials_ciphertext = encrypt_secret(raw)
        self.credentials_last4 = raw[-4:]

    def get_credentials(self) -> str:
        """Plaintext token, or "" when the encryption key changed."""
        try:
            return decrypt_secret(self.credentials_ciphertext)
        except ValueError:
            return ""

    # -- webhook path key (displayable) ------------------------------------
    def set_webhook_key(self, raw: str) -> None:
        self.webhook_key_ciphertext = encrypt_secret(raw) if raw else ""

    def get_webhook_key(self) -> str:
        try:
            return decrypt_secret(self.webhook_key_ciphertext)
        except ValueError:
            return ""

    # -- webhook header secret (verify-only) -------------------------------
    def set_webhook_secret(self, raw: str) -> None:
        self.webhook_secret_digest = hmac_digest(raw) if raw else ""

    def verify_webhook_secret(self, presented: str) -> bool:
        if not self.webhook_secret_digest:
            return False
        return verify_digest(presented or "", self.webhook_secret_digest)

    def mark_active(self) -> None:
        self.status = self.Status.ACTIVE
        self.webhook_registered_at = timezone.now()
        self.webhook_error = ""
        self.disabled_at = None

    def mark_error(self, reason: str) -> None:
        self.status = self.Status.ERROR
        # Fixed, short, and never derived from an exception string.
        self.webhook_error = (reason or "")[:200]


class ChannelIdentity(UUIDModel, TimeStampedModel):
    """One external user on one channel.

    Deliberately stores no display name, username or locale: the numeric id is
    already pseudonymous and is all routing and limiting need. Anything more is
    PII we would have to delete later.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="channel_identities"
    )
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="identities")
    external_user_id = models.CharField(max_length=64)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)

    class Meta:
        db_table = "channels_channel_identity"
        ordering = ("-last_seen_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_user_id"], name="uniq_channel_identity"
            ),
        ]

    def __str__(self) -> str:
        return f"identity {self.external_user_id} on {self.channel_id}"


class ChannelThread(UUIDModel, TimeStampedModel):
    """An external chat mapped to a conversation.

    Kept separate from ``Conversation`` so retention can anonymise the
    conversation without breaking routing: the thread simply re-points.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="channel_threads"
    )
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="threads")
    identity = models.ForeignKey(
        ChannelIdentity, on_delete=models.SET_NULL, null=True, blank=True, related_name="threads"
    )
    external_thread_id = models.CharField(max_length=64)
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="channel_threads",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "channels_channel_thread"
        ordering = ("-last_message_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_thread_id"], name="uniq_channel_thread"
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "-last_message_at"], name="channel_thread_recent_idx"),
        ]

    def __str__(self) -> str:
        return f"thread {self.external_thread_id} on {self.channel_id}"


class ChannelEvent(UUIDModel):
    """Inbound ledger: one row per provider update, used for dedupe and delivery state."""

    class Kind(models.TextChoices):
        MESSAGE = "MESSAGE", "Message"
        OTHER = "OTHER", "Other update type"

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSING = "PROCESSING", "Processing"
        SENDING = "SENDING", "Sending"
        REPLIED = "REPLIED", "Replied"
        IGNORED = "IGNORED", "Ignored"
        FAILED = "FAILED", "Failed"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="channel_events"
    )
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="events")
    update_id = models.BigIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.MESSAGE)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )

    external_thread_id = models.CharField(max_length=64, blank=True)
    external_message_id = models.CharField(max_length=64, blank=True)
    external_user_id = models.CharField(max_length=64, blank=True)

    reply_message_ids = models.JSONField(default=list, blank=True)
    error_code = models.CharField(max_length=48, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    enqueued_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "channels_channel_event"
        ordering = ("-created_at",)
        constraints = [
            # Telegram update_id is monotonic per bot, so uniqueness is per channel.
            models.UniqueConstraint(fields=["channel", "update_id"], name="uniq_channel_update"),
        ]
        indexes = [
            models.Index(fields=["channel", "status", "created_at"], name="channel_event_state_idx"),
            models.Index(fields=["organization", "-created_at"], name="channel_event_org_idx"),
        ]

    def __str__(self) -> str:
        return f"event {self.update_id} ({self.status})"

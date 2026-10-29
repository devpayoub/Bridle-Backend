"""Messaging channels: a workspace's assistant answering inside a chat app."""
from __future__ import annotations

from django.apps import AppConfig


class ChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.channels"
    verbose_name = "Messaging channels"

    def ready(self) -> None:
        from apps.channels import checks  # noqa: F401  (registers system checks)

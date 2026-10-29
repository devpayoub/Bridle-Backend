"""Re-register (or delete) Telegram webhooks after a key rotation.

Rotating ``SECRET_KEY`` invalidates every stored webhook secret; rotating
``AI_CREDENTIALS_KEY`` invalidates every bot token. Both look identical from
Telegram's side: the webhook starts returning 403 and messages stop arriving.
This command is the recovery path, and the emergency stop.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.channels.models import Channel
from apps.channels.registry import get_adapter
from apps.channels.services import webhook_url_for
from apps.common.crypto import generate_secret


class Command(BaseCommand):
    help = "Re-register channel webhooks (repairs secret rotation) or delete them all."

    def add_arguments(self, parser):
        parser.add_argument("--delete-all", action="store_true", help="Delete every webhook instead of re-registering.")
        parser.add_argument("--channel", help="Limit to one channel id.")

    def handle(self, *args, **options):
        queryset = Channel.objects.filter(status=Channel.Status.ACTIVE).select_related("organization")
        if options["channel"]:
            queryset = queryset.filter(id=options["channel"])

        if not queryset.exists():
            self.stdout.write("No active channels.")
            return

        for channel in queryset:
            adapter = get_adapter(channel.type)
            if options["delete_all"]:
                adapter.deregister(channel)
                channel.status = Channel.Status.DISABLED
                channel.save(update_fields=["status", "updated_at"])
                self.stdout.write(f"deleted webhook · {channel.id} · {channel.name}")
                continue

            if not channel.has_credentials:
                channel.mark_error("credentials_unreadable")
                channel.save(update_fields=["status", "webhook_error", "updated_at"])
                self.stdout.write(self.style.WARNING(f"no readable token · {channel.id} · reconnect required"))
                continue

            # A fresh path key as well: the old URL may be the thing that leaked.
            channel.set_webhook_key(generate_secret(32))
            secret = generate_secret(32)
            url = webhook_url_for(channel)
            try:
                adapter.register(channel, url, secret)
            except Exception as exc:  # noqa: BLE001 - report and continue
                channel.mark_error(getattr(exc, "code", "registration_failed"))
                channel.save(update_fields=["webhook_key_ciphertext", "status", "webhook_error", "updated_at"])
                self.stdout.write(self.style.ERROR(f"failed · {channel.id} · {exc}"))
                continue

            channel.set_webhook_secret(secret)
            channel.mark_active()
            channel.save(
                update_fields=[
                    "webhook_key_ciphertext",
                    "webhook_secret_digest",
                    "status",
                    "webhook_registered_at",
                    "webhook_error",
                    "disabled_at",
                    "updated_at",
                ]
            )
            self.stdout.write(self.style.SUCCESS(f"re-registered · {channel.id} · {channel.name}"))

"""System checks for the channels app.

Both are deployment mistakes that would otherwise surface as a silent failure at
the worst possible moment — a client's bot that never receives a message.
"""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_public_base_url(app_configs, **kwargs):
    if not settings.CHANNELS_ENABLED:
        return []
    if not settings.CHANNELS_PUBLIC_BASE_URL:
        return [
            Warning(
                "CHANNELS_ENABLED is on but CHANNELS_PUBLIC_BASE_URL is empty.",
                hint="Telegram needs a public HTTPS URL to deliver updates to. Connecting a bot will fail until it is set.",
                id="channels.W002",
            )
        ]
    if not settings.CHANNELS_PUBLIC_BASE_URL.startswith("https://"):
        return [
            Warning(
                "CHANNELS_PUBLIC_BASE_URL must use https://.",
                hint="Telegram rejects plain-HTTP webhook URLs.",
                id="channels.W003",
            )
        ]
    return []


@register()
def check_credentials_key(app_configs, **kwargs):
    if not settings.CHANNELS_ENABLED:
        return []
    if not getattr(settings, "AI_CREDENTIALS_KEY", ""):
        return [
            Warning(
                "Bot tokens are encrypted with SECRET_KEY because AI_CREDENTIALS_KEY is unset.",
                hint="Set AI_CREDENTIALS_KEY so rotating SECRET_KEY does not invalidate every connected bot.",
                id="channels.W001",
            )
        ]
    return []


@register()
def check_debug_public_exposure(app_configs, **kwargs):
    """A tunnel plus DEBUG is the combination that leaks local variables."""
    if not settings.DEBUG or not settings.CHANNELS_PUBLIC_BASE_URL:
        return []
    return [
        Warning(
            "This API is reachable from the internet (CHANNELS_PUBLIC_BASE_URL is set) with DEBUG on.",
            hint=(
                "Django's technical error page prints the traceback and local variables, "
                "which can include credentials. Run tunnelled sessions with DJANGO_DEBUG=False."
            ),
            id="channels.W004",
        )
    ]

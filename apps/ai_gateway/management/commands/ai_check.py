"""Inspect the effective AI provider configuration and prove it works.

    python manage.py ai_check                 # show what the platform will use
    python manage.py ai_check --call          # ... and send one real request
    python manage.py ai_check --workspace acme --call   # a client's stored key

This is the fastest way to answer "I put the key in .env — is it actually wired?"
after editing environment variables or a client's settings in the console.
"""
from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ai_gateway.base import GenerationRequest, ProviderError, ProviderMessage
from apps.ai_gateway.registry import available_providers, get_provider


def _mask(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


class Command(BaseCommand):
    help = "Show the effective AI provider configuration and optionally test it."

    def add_arguments(self, parser):
        parser.add_argument("--call", action="store_true", help="Send one small request to the provider.")
        parser.add_argument(
            "--workspace",
            default="",
            help="Organization id or slug. Checks that client's stored credentials instead of the environment.",
        )

    def handle(self, *args, **options):
        provider_name = settings.AI_DEFAULT_PROVIDER
        model = settings.AI_DEFAULT_MODEL_NAME
        base_url = ""
        api_key = ""
        source = "environment"
        provider = None
        provider_error = ""

        if options["workspace"]:
            from apps.organizations.models import Organization
            from apps.organizations.services import resolve_provider

            key = options["workspace"]
            organization = (
                Organization.objects.filter(id=key).first()
                if _looks_like_uuid(key)
                else Organization.objects.filter(slug=key).first()
            )
            if organization is None:
                raise CommandError(f"No workspace matches {key!r}.")

            try:
                provider, model, source = resolve_provider(organization)
            except ProviderError as exc:
                raise CommandError(f"Workspace {organization.name}: {exc}") from exc

            provider_name = provider.name
            settings_row = getattr(organization, "ai_settings", None)
            api_key = settings_row.get_api_key() if settings_row and settings_row.has_api_key else ""
            base_url = ""
            self.stdout.write(f"workspace       {organization.name} ({organization.slug})")
        else:
            config = settings.AI_PROVIDER_SETTINGS.get(provider_name, {})
            api_key = config.get("api_key", "")
            base_url = config.get("base_url", "")
            try:
                provider = get_provider(provider_name)
            except ProviderError as exc:
                # Misconfiguration is reported in the table below, not as a traceback.
                provider_error = str(exc)

        self.stdout.write(f"provider        {provider_name}")
        self.stdout.write(f"model           {model}")
        if provider_name == "openai":
            self.stdout.write(f"base_url        {base_url or 'https://api.openai.com/v1 (default)'}")
        self.stdout.write(f"api_key         {_mask(api_key)} ({source})")
        self.stdout.write(f"timeout         {settings.AI_REQUEST_TIMEOUT}s, retries {settings.AI_MAX_RETRIES}")
        self.stdout.write(f"embeddings      {settings.EMBEDDING_PROVIDER}")
        self.stdout.write(
            f"credentials key {'set' if settings.AI_CREDENTIALS_KEY else 'not set (falls back to SECRET_KEY)'}"
        )
        self.stdout.write(f"available       {', '.join(available_providers())}")

        if provider_error:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(provider_error))

        if not options["call"]:
            self.stdout.write("")
            self.stdout.write("Add --call to send a real request.")
            return

        if provider is None:
            raise CommandError(provider_error or "The provider is not configured.")

        started = time.monotonic()
        try:
            result = provider.generate_response(
                GenerationRequest(
                    model=model,
                    messages=[ProviderMessage(role="user", content="Reply with the single word: ok")],
                    max_output_tokens=16,
                    temperature=0.0,
                )
            )
        except ProviderError as exc:
            raise CommandError(f"{exc.code}: {exc}") from exc

        elapsed = int((time.monotonic() - started) * 1000)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"call ok         {elapsed} ms"))
        self.stdout.write(f"reply           {result.text.strip()[:200]}")
        self.stdout.write(
            f"tokens          in {result.input_tokens}, out {result.output_tokens}"
            f"{' (estimated)' if result.usage_estimated else ''}"
        )


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True

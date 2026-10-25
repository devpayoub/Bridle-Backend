"""Provider registry.

Adding a provider:

1. Implement ``AIProvider`` in a new module.
2. Register a factory in ``_FACTORIES``.
3. Set ``AI_DEFAULT_PROVIDER`` (and optionally ``EMBEDDING_PROVIDER``).
4. Add a contract test that exercises the adapter against recorded fixtures.

The registry is deliberately tiny: no plugin discovery, no dynamic imports from
user input.
"""
from __future__ import annotations

import logging
import threading

from django.conf import settings

from apps.ai_gateway.base import AIProvider, ProviderUnavailable

logger = logging.getLogger("apps.ai_gateway.registry")

_lock = threading.Lock()
_cache: dict[str, AIProvider] = {}
_overrides: dict[str, AIProvider] = {}


def _build(name: str, **credentials) -> AIProvider:
    if name == "mock":
        from apps.ai_gateway.mock import MockProvider

        return MockProvider()
    if name == "openai":
        from apps.ai_gateway.openai_provider import OpenAIProvider

        return OpenAIProvider(**credentials)
    raise ProviderUnavailable(f"Unknown AI provider: {name}")


def get_provider(name: str | None = None, *, api_key: str | None = None) -> AIProvider:
    """Resolve a provider adapter.

    ``api_key`` comes from a workspace's stored credentials. Credentialed instances
    are never cached: the cache is keyed by provider name only, so caching them
    would leak one tenant's key into another tenant's request.
    """
    resolved = name or settings.AI_DEFAULT_PROVIDER
    if api_key:
        return _build(resolved, api_key=api_key)
    if resolved in _overrides:
        return _overrides[resolved]
    with _lock:
        provider = _cache.get(resolved)
        if provider is None:
            provider = _build(resolved)
            _cache[resolved] = provider
        return provider


def get_embedding_provider() -> AIProvider:
    return get_provider(settings.EMBEDDING_PROVIDER)


# ---------------------------------------------------------------------------
# Test / development helpers
# ---------------------------------------------------------------------------
def set_provider(name: str, provider: AIProvider) -> None:
    """Override a provider instance (used by tests)."""
    _overrides[name] = provider


def reset_providers() -> None:
    with _lock:
        _cache.clear()
    _overrides.clear()


def available_providers() -> list[str]:
    return ["mock", "openai"]


def provider_labels() -> dict[str, str]:
    """Display names for the console.

    ``openai`` is the wire protocol (any OpenAI-compatible gateway speaks it), not
    necessarily the vendor. Set ``AI_PROVIDER_LABEL`` to name the actual provider.
    """
    labels = {"mock": "Mock (no API calls)", "openai": "OpenAI-compatible"}
    custom = getattr(settings, "AI_PROVIDER_LABEL", "")
    if custom:
        labels[settings.AI_DEFAULT_PROVIDER] = custom
    return labels

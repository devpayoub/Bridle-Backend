"""Provider-independent contracts for every AI capability the platform needs.

Nothing in this module imports a vendor SDK. Application code depends on these
types only, which keeps the policy engine and chat pipeline vendor neutral.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Base class for normalised provider failures."""

    code = "provider_error"
    retryable = False

    def __init__(self, message: str = "AI provider error", *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    retryable = True


class ProviderRateLimit(ProviderError):
    code = "provider_rate_limited"
    retryable = True


class ProviderQuotaExceeded(ProviderError):
    """The account behind the key has no spendable credit.

    Gateways often return this as HTTP 429 with ``insufficient_quota`` or
    ``card_required``; retrying will not help until someone tops up.
    """

    code = "provider_quota_exceeded"
    retryable = False


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    retryable = True


class ProviderAuthError(ProviderError):
    code = "provider_auth_error"
    retryable = False


class ProviderInvalidRequest(ProviderError):
    code = "provider_invalid_request"
    retryable = False


class ProviderContentFiltered(ProviderError):
    code = "provider_content_filtered"
    retryable = False


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    messages: Sequence[ProviderMessage]
    max_output_tokens: int = 600
    temperature: float = 0.2
    top_p: float | None = None
    timeout: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    latency_ms: int = 0
    usage_estimated: bool = True
    provider_request_id: str = ""
    raw_usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassificationResult:
    """Vendor-neutral result of a classification call."""

    label: str
    confidence: float
    rationale_code: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    usage_tokens: int = 0
    usage_estimated: bool = True


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
class AIProvider(ABC):
    """Every provider adapter implements this surface."""

    name: str = "base"
    supports_classification = False

    @abstractmethod
    def generate_response(self, request: GenerationRequest) -> GenerationResult:
        """Generate a chat completion."""

    def classify_request(self, *, text: str, spec: dict, timeout: float | None = None) -> ClassificationResult:
        """Classify a user request against a domain specification.

        Providers that cannot classify should raise ``NotImplementedError``; the
        policy engine falls back to deterministic rules.
        """
        raise NotImplementedError

    def evaluate_response(self, *, text: str, spec: dict, timeout: float | None = None) -> ClassificationResult:
        raise NotImplementedError

    @abstractmethod
    def create_embeddings(self, *, texts: list[str], model: str | None = None) -> EmbeddingResult:
        """Embed a batch of texts."""

    def get_usage_information(self) -> dict[str, Any]:
        """Provider metadata for diagnostics (never includes credentials)."""
        return {"provider": self.name, "classification": self.supports_classification}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token).

    Only used when a provider does not return authoritative usage. The
    ``usage_estimated`` flag propagates so downstream reporting never presents
    estimates as exact numbers.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)

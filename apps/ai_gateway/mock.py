"""Deterministic mock provider.

Used by the automated test suite and by local development so no paid API calls
are required. It is intentionally *not* random: identical input always produces
identical output, which makes assertions stable.
"""
from __future__ import annotations

import hashlib
import math

from django.conf import settings

from apps.ai_gateway.base import (
    AIProvider,
    ClassificationResult,
    EmbeddingResult,
    GenerationRequest,
    GenerationResult,
    estimate_tokens,
)

_OUT_OF_DOMAIN_MARKERS = (
    "weather",
    "football",
    "stock price",
    "who won",
    "write me a poem",
    "bitcoin",
)
_AMBIGUOUS_MARKERS = ("maybe", "not sure", "something", "you know")


class MockProvider(AIProvider):
    name = "mock"
    supports_classification = True

    def __init__(self, *, response_text: str | None = None, dimensions: int | None = None):
        self._response_text = response_text
        self._dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
        self.calls: list[GenerationRequest] = []

    # -- generation ------------------------------------------------------
    def generate_response(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        if self._response_text is not None:
            text = self._response_text
        else:
            text = (
                f"[mock:{request.model}] This is a deterministic mock answer. "
                "It is generated locally and is not produced by a language model."
            )
        prompt_chars = sum(len(m.content) for m in request.messages)
        return GenerationResult(
            text=text,
            provider=self.name,
            model=request.model,
            input_tokens=estimate_tokens("x" * prompt_chars),
            output_tokens=estimate_tokens(text),
            finish_reason="stop",
            latency_ms=1,
            usage_estimated=True,
            provider_request_id="mock-" + hashlib.sha256(text.encode()).hexdigest()[:16],
            raw_usage={"mock": True},
        )

    # -- classification --------------------------------------------------
    def classify_request(self, *, text: str, spec: dict, timeout: float | None = None) -> ClassificationResult:
        lowered = (text or "").lower()
        if any(marker in lowered for marker in _OUT_OF_DOMAIN_MARKERS):
            return ClassificationResult(
                label="OUT_OF_DOMAIN", confidence=0.93, rationale_code="MOCK_OUT_OF_DOMAIN", provider=self.name
            )
        if any(marker in lowered for marker in _AMBIGUOUS_MARKERS):
            return ClassificationResult(
                label="AMBIGUOUS", confidence=0.55, rationale_code="MOCK_AMBIGUOUS", provider=self.name
            )
        return ClassificationResult(
            label="IN_DOMAIN", confidence=0.88, rationale_code="MOCK_IN_DOMAIN", provider=self.name
        )

    def evaluate_response(self, *, text: str, spec: dict, timeout: float | None = None) -> ClassificationResult:
        if "guaranteed" in (text or "").lower():
            return ClassificationResult(
                label="VIOLATION", confidence=0.9, rationale_code="MOCK_UNSUPPORTED_PROMISE", provider=self.name
            )
        return ClassificationResult(label="OK", confidence=0.9, rationale_code="MOCK_OK", provider=self.name)

    # -- embeddings ------------------------------------------------------
    def create_embeddings(self, *, texts: list[str], model: str | None = None) -> EmbeddingResult:
        vectors = [_deterministic_vector(text, self._dimensions) for text in texts]
        return EmbeddingResult(
            vectors=vectors,
            provider=self.name,
            model=model or "mock-embedding",
            dimensions=self._dimensions,
            usage_tokens=sum(estimate_tokens(t) for t in texts),
            usage_estimated=True,
        )

    def get_usage_information(self) -> dict:
        return {
            "provider": self.name,
            "classification": True,
            "deterministic": True,
            "billing": "none",
        }


def _deterministic_vector(text: str, dimensions: int) -> list[float]:
    """Map text to a unit vector using repeated SHA-256 expansion."""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for byte in block:
            values.append((byte - 127.5) / 127.5)
            if len(values) == dimensions:
                break
        counter += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]

"""OpenAI adapter tests using a stubbed SDK client (no network calls)."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from apps.ai_gateway.base import (
    GenerationRequest,
    ProviderAuthError,
    ProviderContentFiltered,
    ProviderInvalidRequest,
    ProviderMessage,
    ProviderRateLimit,
    ProviderTimeout,
    ProviderUnavailable,
)
from apps.ai_gateway.openai_provider import OpenAIProvider, _extract_json


def _request():
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status: int, headers: dict | None = None):
    return httpx.Response(status, request=_request(), headers=headers or {})


class FakeCompletions:
    def __init__(self, *, text="Hello", finish_reason="stop", error=None, usage=True):
        self.text = text
        self.finish_reason = finish_reason
        self.error = error
        self.usage = usage
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7) if self.usage else None
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.text), finish_reason=self.finish_reason
                )
            ],
            model=kwargs.get("model", "gpt-4o-mini"),
            usage=usage,
            id="req_test",
        )


class FakeEmbeddings:
    def __init__(self, dimensions=8, error=None):
        self.dimensions = dimensions
        self.error = error

    def create(self, **kwargs):
        if self.error is not None:
            raise self.error
        texts = kwargs["input"]
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * self.dimensions) for _ in texts],
            usage=SimpleNamespace(total_tokens=len(texts) * 3),
        )


class FakeOpenAI:
    def __init__(self, *, completions=None, embeddings=None, **kwargs):
        self.chat = SimpleNamespace(completions=completions or FakeCompletions())
        self.embeddings = embeddings or FakeEmbeddings()
        self.init_kwargs = kwargs


@pytest.fixture
def make_provider(monkeypatch, settings):
    settings.AI_PROVIDER_SETTINGS = {
        **settings.AI_PROVIDER_SETTINGS,
        "openai": {"api_key": "sk-test-key", "base_url": "", "organization": ""},
    }

    def _factory(**client_kwargs):
        fake = FakeOpenAI(**client_kwargs)
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: fake)
        return OpenAIProvider(), fake

    return _factory


def _generation_request(model="gpt-4o-mini"):
    return GenerationRequest(
        model=model,
        messages=[ProviderMessage(role="user", content="What time is check-in?")],
        max_output_tokens=100,
        temperature=0.1,
    )


def test_requires_api_key(settings):
    settings.AI_PROVIDER_SETTINGS = {"openai": {"api_key": "", "base_url": "", "organization": ""}}
    with pytest.raises(ProviderAuthError):
        OpenAIProvider()


def test_generate_response_maps_usage(make_provider):
    provider, fake = make_provider()
    result = provider.generate_response(_generation_request())

    assert result.text == "Hello"
    assert result.provider == "openai"
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.usage_estimated is False
    assert result.provider_request_id == "req_test"
    assert fake.chat.completions.calls[0]["max_tokens"] == 100


def test_generate_response_estimates_without_usage(make_provider):
    provider, _ = make_provider(completions=FakeCompletions(usage=False))
    result = provider.generate_response(_generation_request())
    assert result.usage_estimated is True
    assert result.input_tokens > 0


def test_content_filter_finish_reason_raises(make_provider):
    provider, _ = make_provider(completions=FakeCompletions(finish_reason="content_filter"))
    with pytest.raises(ProviderContentFiltered):
        provider.generate_response(_generation_request())


@pytest.mark.parametrize(
    "error,expected",
    [
        (openai.APITimeoutError(request=_request()), ProviderTimeout),
        (openai.RateLimitError("slow down", response=_response(429, {"retry-after": "30"}), body=None), ProviderRateLimit),
        (openai.AuthenticationError("bad key", response=_response(401), body=None), ProviderAuthError),
        (openai.BadRequestError("bad payload", response=_response(400), body=None), ProviderInvalidRequest),
        (openai.APIConnectionError(request=_request()), ProviderUnavailable),
        (openai.InternalServerError("boom", response=_response(500), body=None), ProviderUnavailable),
    ],
)
def test_errors_are_normalised(make_provider, error, expected):
    provider, _ = make_provider(completions=FakeCompletions(error=error))
    with pytest.raises(expected):
        provider.generate_response(_generation_request())


def test_rate_limit_retry_after_is_propagated(make_provider):
    error = openai.RateLimitError("slow down", response=_response(429, {"retry-after": "42"}), body=None)
    provider, _ = make_provider(completions=FakeCompletions(error=error))
    with pytest.raises(ProviderRateLimit) as exc_info:
        provider.generate_response(_generation_request())
    assert exc_info.value.retry_after == 42


def test_create_embeddings(make_provider):
    provider, _ = make_provider(embeddings=FakeEmbeddings(dimensions=4))
    result = provider.create_embeddings(texts=["a", "b"])
    assert len(result.vectors) == 2
    assert result.dimensions == 4
    assert result.usage_tokens == 6


def test_classify_request_parses_json(make_provider):
    provider, _ = make_provider(
        completions=FakeCompletions(text='{"label": "OUT_OF_DOMAIN", "confidence": 0.91, "reason_code": "OFF_TOPIC"}')
    )
    result = provider.classify_request(text="Who won?", spec={"allowed_topics": ["check-in"]})
    assert result.label == "OUT_OF_DOMAIN"
    assert result.confidence == pytest.approx(0.91)
    assert result.rationale_code == "OFF_TOPIC"


def test_classify_request_tolerates_code_fences(make_provider):
    provider, _ = make_provider(
        completions=FakeCompletions(text='```json\n{"label": "IN_DOMAIN", "confidence": 0.8}\n```')
    )
    result = provider.classify_request(text="check-in?", spec={})
    assert result.label == "IN_DOMAIN"


def test_classify_request_handles_garbage(make_provider):
    provider, _ = make_provider(completions=FakeCompletions(text="not json at all"))
    result = provider.classify_request(text="check-in?", spec={})
    assert result.label == ""
    assert result.confidence == 0.0


def test_usage_information_has_no_secrets(make_provider):
    provider, _ = make_provider()
    info = provider.get_usage_information()
    assert "sk-test-key" not in str(info)


@pytest.mark.parametrize(
    "raw,expected_label",
    [
        ('{"label": "OK"}', "OK"),
        ('```json\n{"label": "OK"}\n```', "OK"),
        ('prefix {"label": "OK"} suffix', "OK"),
        ("garbage", ""),
        ("", ""),
    ],
)
def test_extract_json(raw, expected_label):
    assert _extract_json(raw).get("label", "") == expected_label

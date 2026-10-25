"""Retry/backoff helper tests."""
from __future__ import annotations

import pytest

from apps.ai_gateway.base import ProviderInvalidRequest, ProviderRateLimit, ProviderTimeout
from apps.ai_gateway.retry import with_retry


class Recorder:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_returns_on_first_success():
    recorder = Recorder()
    assert with_retry(lambda: "ok", sleep=recorder) == "ok"
    assert recorder.delays == []


def test_retries_transient_failures_then_succeeds():
    recorder = Recorder()
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ProviderTimeout("timeout")
        return "ok"

    assert with_retry(flaky, attempts=3, base_delay=1.0, sleep=recorder, jitter=lambda: 0.0) == "ok"
    assert attempts["count"] == 3
    assert recorder.delays == [1.0, 2.0]


def test_raises_after_exhausting_attempts():
    recorder = Recorder()
    attempts = {"count": 0}

    def always_fails():
        attempts["count"] += 1
        raise ProviderTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        with_retry(always_fails, attempts=3, base_delay=0.5, sleep=recorder, jitter=lambda: 0.0)
    assert attempts["count"] == 3
    assert len(recorder.delays) == 2


def test_non_retryable_error_is_raised_immediately():
    recorder = Recorder()
    attempts = {"count": 0}

    def bad_request():
        attempts["count"] += 1
        raise ProviderInvalidRequest("bad payload")

    with pytest.raises(ProviderInvalidRequest):
        with_retry(bad_request, attempts=5, sleep=recorder)
    assert attempts["count"] == 1
    assert recorder.delays == []


def test_retry_after_header_is_honoured():
    recorder = Recorder()
    attempts = {"count": 0}

    def rate_limited():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ProviderRateLimit("slow down", retry_after=30)
        return "ok"

    assert with_retry(rate_limited, attempts=2, base_delay=0.5, sleep=recorder, jitter=lambda: 0.0) == "ok"
    assert recorder.delays[0] >= 30


def test_backoff_is_capped():
    recorder = Recorder()
    attempts = {"count": 0}

    def always_fails():
        attempts["count"] += 1
        raise ProviderTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        with_retry(always_fails, attempts=5, base_delay=10.0, max_delay=15.0, sleep=recorder, jitter=lambda: 0.0)
    assert all(delay <= 15.0 for delay in recorder.delays)

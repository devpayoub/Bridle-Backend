"""Log hygiene: secrets must not survive into a log sink.

These are regression tests for the audit finding that a Telegram bot token reached
the worker log through httpx's INFO line, because the token lives in the request
URL (``/bot<id>:<secret>/sendMessage``).
"""
from __future__ import annotations

import logging

import pytest

from apps.common.logging import SecretRedactionFilter, redact

BOT_TOKEN = "bot8876276056:" + "A" * 35


class TestRedactPatterns:
    def test_telegram_token_in_a_url_is_masked(self):
        line = f"HTTP Request: POST https://api.telegram.org/{BOT_TOKEN}/sendMessage"
        masked = redact(line)
        assert "AAAA" not in masked
        assert "bot[REDACTED]" in masked or "[REDACTED_TELEGRAM_TOKEN]" in masked

    def test_bare_telegram_token_is_masked(self):
        assert "AAAA" not in redact(f"token={BOT_TOKEN}")

    def test_webhook_path_key_is_masked(self):
        url = "/webhooks/telegram/6d7bafd4-a0dd-4696-93f6-4f6291dfd1d0/ue5qLEn6oExaWJ4yaeXyGhwAXD_cfFRIzazA/"
        masked = redact(url)
        assert "ue5qLEn6oExaWJ4yaeXyGhwAXD_cfFRIzazA" not in masked
        assert "[REDACTED]" in masked
        # The channel id stays readable so the log line is still useful.
        assert "6d7bafd4-a0dd-4696-93f6-4f6291dfd1d0" in masked

    def test_provider_key_is_masked(self):
        assert "abcdefghijklmnop" not in redact("api_key=sk-abcdefghijklmnopqrstuvwxyz123456")

    def test_bearer_header_is_masked(self):
        assert "eyJhbGciOi" not in redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")

    def test_ordinary_text_is_untouched(self):
        line = "channel.dispatch.replied channel=abc decision=ALLOW latency_ms=42"
        assert redact(line) == line


class TestFilter:
    def test_filter_masks_the_formatted_message(self):
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="POST https://api.telegram.org/%s/sendMessage",
            args=(BOT_TOKEN,),
            exc_info=None,
        )
        SecretRedactionFilter().filter(record)
        message = record.getMessage()
        assert "AAAA" not in message
        # args are consumed, so a later formatter cannot resurrect the raw value
        assert record.args == ()


@pytest.mark.django_db
class TestReadinessDisclosure:
    def test_ready_does_not_advertise_features(self, api_client):
        response = api_client.get("/api/v1/ready/")
        assert response.status_code in {200, 503}
        assert "vector_search" not in response.data
        assert set(response.data) == {"status", "checks"}

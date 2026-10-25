"""Unit tests for quota reservation and rate limiting."""
from __future__ import annotations

import pytest

from apps.billing.models import UsageLimit
from apps.common.exceptions import QuotaExceeded, RateLimitExceeded
from apps.usage.quota import METRIC_CHAT, get_quota_state, reserve_quota
from apps.usage.rate_limit import check_rate_limit, enforce_rate_limit, reset_rate_limit


class TestQuota:
    def test_reservation_increments_counter(self, organization):
        with reserve_quota(organization, METRIC_CHAT):
            pass
        assert get_quota_state(organization, METRIC_CHAT).used == 1

    def test_failed_operation_releases_reservation(self, organization):
        with pytest.raises(RuntimeError):
            with reserve_quota(organization, METRIC_CHAT):
                raise RuntimeError("provider blew up")
        assert get_quota_state(organization, METRIC_CHAT).used == 0

    def test_limit_exceeded_raises_with_retry_after(self, organization):
        UsageLimit.objects.create(
            organization=organization, metric=UsageLimit.Metric.CHAT_REQUESTS, limit_value=1
        )
        with reserve_quota(organization, METRIC_CHAT):
            pass
        with pytest.raises(QuotaExceeded) as exc_info:
            with reserve_quota(organization, METRIC_CHAT):
                pass
        assert getattr(exc_info.value, "retry_after", 0) > 0
        assert exc_info.value.extra["limit"] == 1

    def test_state_reports_remaining(self, organization):
        UsageLimit.objects.create(
            organization=organization, metric=UsageLimit.Metric.CHAT_REQUESTS, limit_value=5
        )
        with reserve_quota(organization, METRIC_CHAT):
            pass
        state = get_quota_state(organization, METRIC_CHAT)
        assert state.limit == 5
        assert state.remaining == 4

    def test_unknown_metric_rejected(self, organization):
        with pytest.raises(ValueError):
            with reserve_quota(organization, "not_a_metric"):
                pass


class TestRateLimit:
    def test_allows_until_limit(self, settings):
        settings.RATE_LIMITS = {"test.scope": {"limit": 2, "window": 60}}
        assert check_rate_limit("test.scope", "client").allowed
        assert check_rate_limit("test.scope", "client").allowed
        result = check_rate_limit("test.scope", "client")
        assert result.allowed is False
        assert result.retry_after > 0

    def test_identifiers_are_isolated(self, settings):
        settings.RATE_LIMITS = {"test.scope": {"limit": 1, "window": 60}}
        assert check_rate_limit("test.scope", "a").allowed
        assert check_rate_limit("test.scope", "b").allowed
        assert not check_rate_limit("test.scope", "a").allowed

    def test_reset_clears_window(self, settings):
        settings.RATE_LIMITS = {"test.scope": {"limit": 1, "window": 60}}
        assert check_rate_limit("test.scope", "a").allowed
        reset_rate_limit("test.scope", "a")
        assert check_rate_limit("test.scope", "a").allowed

    def test_enforce_raises_platform_error(self, settings):
        settings.RATE_LIMITS = {"test.scope": {"limit": 1, "window": 60}}
        enforce_rate_limit("test.scope", "a")
        with pytest.raises(RateLimitExceeded):
            enforce_rate_limit("test.scope", "a")

    def test_disabled_limits_always_allow(self, settings):
        settings.RATE_LIMITS = {"test.scope": {"limit": 1, "window": 60}}
        settings.RATE_LIMIT_ENABLED = False
        for _ in range(5):
            assert check_rate_limit("test.scope", "a").allowed

    def test_unknown_scope_allows(self):
        assert check_rate_limit("unknown.scope", "a").allowed

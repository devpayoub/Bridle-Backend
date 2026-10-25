"""Payment provider integration seam.

No payment provider is implemented in this build. The core chat and policy
product does not depend on billing: subscriptions are managed internally and
entitlements are enforced from ``apps.billing.models``.

To add a provider (Stripe, Paddle, ...):

1. Implement ``PaymentProvider`` in a new module, e.g. ``stripe_provider.py``.
2. Verify webhook signatures with the provider SDK **before** parsing events.
3. Map provider events onto ``Subscription`` mutations and record the provider
   identifiers (``external_customer_id`` / ``external_subscription_id``).
4. Make webhook handling idempotent: store the provider event id and ignore
   duplicates (see ``WebhookEvent`` below).
5. Never store card data; use the provider's hosted checkout / customer portal.

This module is intentionally free of provider-specific code so that a payment
gateway failure can never break chat traffic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    """Minimal contract every payment integration must satisfy."""

    @abstractmethod
    def create_customer(self, *, organization_id: str, email: str) -> str:
        """Return the provider customer id."""

    @abstractmethod
    def create_checkout_session(self, *, customer_id: str, plan_code: str, success_url: str) -> str:
        """Return a hosted checkout URL."""

    @abstractmethod
    def verify_webhook(self, *, payload: bytes, signature: str) -> dict:
        """Verify the signature and return the parsed event.

        Must raise ``ValueError`` when the signature is invalid.
        """

    @abstractmethod
    def cancel_subscription(self, *, external_subscription_id: str) -> None:
        """Cancel at the provider."""


class ProviderNotConfigured(RuntimeError):
    """Raised when a billing integration is requested but not configured."""


def get_payment_provider() -> PaymentProvider:
    raise ProviderNotConfigured(
        "No payment provider is configured. Billing is internal-only in this build; "
        "see apps/billing/providers.py for the integration contract."
    )

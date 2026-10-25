"""Default plans seeded by data migration."""
from __future__ import annotations

DEFAULT_PLANS: list[dict] = [
    {
        "code": "free",
        "name": "Free",
        "description": "Evaluation plan for a single assistant.",
        "price_monthly_cents": 0,
        "max_assistants": 1,
        "max_monthly_chat_requests": 1_000,
        "max_monthly_policy_evaluations": 1_000,
        "max_knowledge_bytes": 25 * 1024 * 1024,
        "max_members": 3,
        "max_api_keys": 5,
        "is_default": True,
    },
    {
        "code": "starter",
        "name": "Starter",
        "description": "For small teams shipping a first assistant.",
        "price_monthly_cents": 4_900,
        "max_assistants": 5,
        "max_monthly_chat_requests": 50_000,
        "max_monthly_policy_evaluations": 50_000,
        "max_knowledge_bytes": 500 * 1024 * 1024,
        "max_members": 10,
        "max_api_keys": 20,
    },
    {
        "code": "business",
        "name": "Business",
        "description": "Higher limits, audit access and priority support.",
        "price_monthly_cents": 24_900,
        "max_assistants": 50,
        "max_monthly_chat_requests": 500_000,
        "max_monthly_policy_evaluations": 500_000,
        "max_knowledge_bytes": 5 * 1024 * 1024 * 1024,
        "max_members": 50,
        "max_api_keys": 100,
    },
]

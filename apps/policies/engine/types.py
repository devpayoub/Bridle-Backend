"""Core policy engine types.

The engine exposes a small, stable vocabulary so that alternative evaluators
(rules today, model-assisted or vendor-provided later) can be swapped without
touching the chat pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"
    REVIEW = "REVIEW"


class ReasonCode(str, Enum):
    ALLOWED = "ALLOWED"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    PROHIBITED_TOPIC = "PROHIBITED_TOPIC"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    INSUFFICIENT_KNOWLEDGE = "INSUFFICIENT_KNOWLEDGE"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    RESPONSE_POLICY_VIOLATION = "RESPONSE_POLICY_VIOLATION"
    PII_DETECTED = "PII_DETECTED"
    INVALID_INPUT = "INVALID_INPUT"
    EVALUATOR_UNAVAILABLE = "EVALUATOR_UNAVAILABLE"
    TOOL_NOT_PERMITTED = "TOOL_NOT_PERMITTED"


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason_code: ReasonCode
    user_safe_message: str
    evaluator: str
    evaluator_version: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allows_generation(self) -> bool:
        return self.decision == Decision.ALLOW

    def to_api(self, *, policy_version: int = 0) -> dict[str, Any]:
        """Serialise for API responses. Internal scores stay in ``metadata`` only."""
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "message": self.user_safe_message,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "policy_version": policy_version,
            "evaluator": self.evaluator,
            "evaluator_version": self.evaluator_version,
        }


@dataclass(frozen=True)
class PolicySpec:
    """Everything an evaluator needs, independent of Django models."""

    allowed_topics: list[str] = field(default_factory=list)
    prohibited_topics: list[str] = field(default_factory=list)
    escalation_keywords: list[str] = field(default_factory=list)
    response_constraints: dict[str, Any] = field(default_factory=dict)
    knowledge_required: bool = False
    uncertainty_handling: str = "CLARIFY"
    allow_threshold: float = 0.60
    clarify_threshold: float = 0.34
    industry: str = ""
    policy_version: int = 0
    evaluator_mode: str = "RULES"
    fallback_message: str = "I can only help with questions about our services."

    @classmethod
    def from_assistant(cls, assistant, policy=None) -> "PolicySpec":
        from django.conf import settings

        return cls(
            allowed_topics=list(assistant.allowed_topics or []),
            prohibited_topics=list(assistant.prohibited_topics or []),
            escalation_keywords=list((getattr(policy, "escalation_rules", None) or {}).get("keywords", [])),
            response_constraints=dict(getattr(policy, "response_constraints", None) or {}),
            knowledge_required=bool(assistant.knowledge_required),
            uncertainty_handling=getattr(policy, "uncertainty_handling", "CLARIFY"),
            allow_threshold=float(
                getattr(policy, "allow_threshold", settings.POLICY_DEFAULT_ALLOW_THRESHOLD)
            ),
            clarify_threshold=float(
                getattr(policy, "clarify_threshold", settings.POLICY_DEFAULT_CLARIFY_THRESHOLD)
            ),
            industry=getattr(policy, "industry", "") or assistant.domain_type,
            policy_version=int(getattr(policy, "version", 0) or 0),
            evaluator_mode=getattr(policy, "evaluator_mode", "RULES"),
            fallback_message=assistant.fallback_message,
        )

    def as_classifier_payload(self) -> dict[str, Any]:
        return {
            "allowed_topics": self.allowed_topics,
            "prohibited_topics": self.prohibited_topics,
            "industry": self.industry,
            "response_constraints": self.response_constraints,
        }

"""Optional model-assisted evaluator.

Probabilistic by nature. It is only consulted for requests that deterministic
rules allow (never to overturn a refusal) and its output is always reported with
a confidence value. It is *not* treated as a guarantee.
"""
from __future__ import annotations

import logging

from apps.ai_gateway.base import AIProvider, ProviderError
from apps.policies.engine.rules import user_message
from apps.policies.engine.types import Decision, PolicyDecision, PolicySpec, ReasonCode

logger = logging.getLogger("apps.policies.model_evaluator")

EVALUATOR_NAME = "model"
EVALUATOR_VERSION = "model-1.0.0"

_LABEL_TO_DECISION = {
    "IN_DOMAIN": Decision.ALLOW,
    "OUT_OF_DOMAIN": Decision.REFUSE,
    "AMBIGUOUS": Decision.CLARIFY,
    "ESCALATION_REQUIRED": Decision.ESCALATE,
    "VIOLATION": Decision.REFUSE,
    "OK": Decision.ALLOW,
}


class ModelPolicyEvaluator:
    def __init__(self, provider: AIProvider, *, confidence_threshold: float | None = None):
        from django.conf import settings

        self.provider = provider
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else settings.POLICY_MODEL_CONFIDENCE_THRESHOLD
        )

    def evaluate_request(self, *, spec: PolicySpec, text: str) -> PolicyDecision:
        result = self.provider.classify_request(text=text, spec=spec.as_classifier_payload())
        decision = _LABEL_TO_DECISION.get(result.label)

        if decision is None:
            # Unparseable model output is not an allow: degrade to review.
            return self._decision(
                Decision.REVIEW,
                ReasonCode.AMBIGUOUS_REQUEST,
                spec,
                confidence=result.confidence,
                metadata={"check": "unrecognised_label", "label": result.label[:32]},
            )

        if decision == Decision.ALLOW and result.confidence < self.confidence_threshold:
            return self._decision(
                Decision.CLARIFY,
                ReasonCode.AMBIGUOUS_REQUEST,
                spec,
                confidence=result.confidence,
                metadata={"check": "low_confidence_allow", "threshold": self.confidence_threshold},
            )

        reason = ReasonCode.ALLOWED
        if decision == Decision.REFUSE:
            reason = ReasonCode.OUT_OF_DOMAIN
        elif decision == Decision.CLARIFY:
            reason = ReasonCode.AMBIGUOUS_REQUEST
        elif decision == Decision.ESCALATE:
            reason = ReasonCode.ESCALATION_REQUIRED

        return self._decision(
            decision,
            reason,
            spec,
            confidence=result.confidence,
            metadata={"check": "model_classification", "label": result.label[:32], "rationale": result.rationale_code},
        )

    def evaluate_response(self, *, spec: PolicySpec, text: str) -> PolicyDecision:
        result = self.provider.evaluate_response(text=text, spec=spec.as_classifier_payload())
        if result.label == "VIOLATION" and result.confidence >= self.confidence_threshold:
            return self._decision(
                Decision.REFUSE,
                ReasonCode.RESPONSE_POLICY_VIOLATION,
                spec,
                confidence=result.confidence,
                metadata={"check": "model_response_review", "rationale": result.rationale_code},
            )
        return self._decision(
            Decision.ALLOW,
            ReasonCode.ALLOWED,
            spec,
            confidence=result.confidence,
            metadata={"check": "model_response_review"},
        )

    def _decision(
        self,
        decision: Decision,
        reason: ReasonCode,
        spec: PolicySpec,
        *,
        confidence: float | None,
        metadata: dict,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision=decision,
            reason_code=reason,
            user_safe_message=user_message(spec, reason),
            evaluator=EVALUATOR_NAME,
            evaluator_version=EVALUATOR_VERSION,
            confidence=confidence,
            metadata=metadata,
        )


__all__ = ["ModelPolicyEvaluator", "ProviderError"]

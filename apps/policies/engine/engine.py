"""Policy engine orchestration.

Layering:

    deterministic rules  ->  (optional) model classifier  ->  decision

Rules always run. A model classifier may only *tighten* the outcome, never
loosen a refusal. If a mandatory evaluation cannot be completed the engine
applies the configured fail mode:

* ``closed`` (default): refuse with ``EVALUATOR_UNAVAILABLE``.
* ``open``: return the deterministic result and flag the degradation in
  ``metadata`` — never silent, always logged.

Which is safe for what:

* Chat requests  -> fail closed (a request that cannot be screened is not sent
  to the model).
* Policy evaluation API -> fail closed (callers depend on a decision).
* Response validation -> fail closed (an unvalidated answer is not returned).
"""
from __future__ import annotations

import logging
import time

from django.conf import settings

from apps.policies.engine.model_evaluator import ModelPolicyEvaluator
from apps.policies.engine.rules import RulesPolicyEvaluator, user_message
from apps.policies.engine.types import Decision, PolicyDecision, PolicySpec, ReasonCode

logger = logging.getLogger("apps.policies.engine")


class PolicyEngine:
    def __init__(
        self,
        *,
        rules: RulesPolicyEvaluator | None = None,
        model_evaluator: ModelPolicyEvaluator | None = None,
        fail_mode: str | None = None,
        model_enabled: bool | None = None,
    ):
        self.rules = rules or RulesPolicyEvaluator()
        self.model_evaluator = model_evaluator
        self.fail_mode = (fail_mode or settings.POLICY_FAIL_MODE).lower()
        self.model_enabled = (
            settings.POLICY_MODEL_EVALUATOR_ENABLED if model_enabled is None else model_enabled
        )

    # -- request ---------------------------------------------------------
    def evaluate_request(self, *, spec: PolicySpec, text: str, has_knowledge: bool = False) -> PolicyDecision:
        started = time.monotonic()
        try:
            decision = self.rules.evaluate_request(spec=spec, text=text, has_knowledge=has_knowledge)
        except Exception:
            logger.exception("Deterministic policy evaluation failed")
            return self._unavailable(spec, phase="request")

        if decision.decision != Decision.ALLOW:
            return decision

        if spec.evaluator_mode == "HYBRID" and self.model_enabled:
            if self.model_evaluator is None:
                logger.warning("HYBRID evaluator requested but no model evaluator is configured")
                return self._degrade(spec, decision, reason="model_evaluator_missing")
            try:
                model_decision = self.model_evaluator.evaluate_request(spec=spec, text=text)
            except Exception:
                logger.warning("Model policy evaluator failed", exc_info=True)
                return self._degrade(spec, decision, reason="model_evaluator_error")
            if model_decision.decision != Decision.ALLOW:
                merged_metadata = {**model_decision.metadata, "rules_score": decision.confidence}
                return PolicyDecision(
                    decision=model_decision.decision,
                    reason_code=model_decision.reason_code,
                    user_safe_message=model_decision.user_safe_message,
                    evaluator=model_decision.evaluator,
                    evaluator_version=model_decision.evaluator_version,
                    confidence=model_decision.confidence,
                    metadata=merged_metadata,
                )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return PolicyDecision(
            decision=decision.decision,
            reason_code=decision.reason_code,
            user_safe_message=decision.user_safe_message,
            evaluator=decision.evaluator,
            evaluator_version=decision.evaluator_version,
            confidence=decision.confidence,
            metadata={**decision.metadata, "engine_latency_ms": elapsed_ms},
        )

    # -- response --------------------------------------------------------
    def evaluate_response(self, *, spec: PolicySpec, text: str, context_text: str = "") -> PolicyDecision:
        try:
            decision = self.rules.evaluate_response(spec=spec, text=text, context_text=context_text)
        except Exception:
            logger.exception("Deterministic response evaluation failed")
            return self._unavailable(spec, phase="response")

        if decision.decision != Decision.ALLOW:
            return decision

        if spec.evaluator_mode == "HYBRID" and self.model_enabled and self.model_evaluator is not None:
            try:
                model_decision = self.model_evaluator.evaluate_response(spec=spec, text=text)
            except Exception:
                logger.warning("Model response evaluator failed", exc_info=True)
                return self._degrade(spec, decision, reason="model_response_evaluator_error")
            if model_decision.decision != Decision.ALLOW:
                return model_decision

        return decision

    # -- degradation -----------------------------------------------------
    def _unavailable(self, spec: PolicySpec, *, phase: str) -> PolicyDecision:
        return PolicyDecision(
            decision=Decision.REFUSE,
            reason_code=ReasonCode.EVALUATOR_UNAVAILABLE,
            user_safe_message=user_message(spec, ReasonCode.EVALUATOR_UNAVAILABLE),
            evaluator="engine",
            evaluator_version="engine-1.0.0",
            confidence=None,
            metadata={"phase": phase, "fail_mode": "closed", "degraded": True},
        )

    def _degrade(self, spec: PolicySpec, rules_decision: PolicyDecision, *, reason: str) -> PolicyDecision:
        if self.fail_mode == "open":
            logger.warning("Policy engine degraded to rules-only result (fail_mode=open) reason=%s", reason)
            return PolicyDecision(
                decision=rules_decision.decision,
                reason_code=rules_decision.reason_code,
                user_safe_message=rules_decision.user_safe_message,
                evaluator=rules_decision.evaluator,
                evaluator_version=rules_decision.evaluator_version,
                confidence=rules_decision.confidence,
                metadata={**rules_decision.metadata, "degraded": True, "degrade_reason": reason},
            )
        logger.error("Policy engine failed closed reason=%s", reason)
        return self._unavailable(spec, phase="model")


def build_model_evaluator(provider_name: str | None = None) -> ModelPolicyEvaluator | None:
    """Best-effort construction of the optional model evaluator."""
    if not settings.POLICY_MODEL_EVALUATOR_ENABLED:
        return None
    try:
        from apps.ai_gateway.registry import get_provider

        return ModelPolicyEvaluator(get_provider(provider_name))
    except Exception:
        logger.warning("Unable to build model policy evaluator", exc_info=True)
        return None


def get_policy_engine(*, with_model: bool = True) -> PolicyEngine:
    return PolicyEngine(model_evaluator=build_model_evaluator() if with_model else None)

"""Unit tests for policy engine orchestration and failure semantics."""
from __future__ import annotations

import pytest

from apps.ai_gateway.base import ClassificationResult
from apps.policies.engine.engine import PolicyEngine
from apps.policies.engine.model_evaluator import ModelPolicyEvaluator
from apps.policies.engine.types import Decision, PolicySpec, ReasonCode

SPEC = PolicySpec(
    allowed_topics=["check-in times|checkin", "room service"],
    prohibited_topics=["medical advice"],
    fallback_message="I can only help with questions about our hotel.",
    evaluator_mode="HYBRID",
)


class StubProvider:
    name = "stub"

    def __init__(self, label="IN_DOMAIN", confidence=0.9, raises=False):
        self.label = label
        self.confidence = confidence
        self.raises = raises

    def classify_request(self, *, text, spec, timeout=None):
        if self.raises:
            raise RuntimeError("classifier exploded")
        return ClassificationResult(label=self.label, confidence=self.confidence, provider="stub")

    def evaluate_response(self, *, text, spec, timeout=None):
        if self.raises:
            raise RuntimeError("classifier exploded")
        return ClassificationResult(label=self.label, confidence=self.confidence, provider="stub")


def build_engine(provider, *, fail_mode="closed", model_enabled=True):
    return PolicyEngine(
        model_evaluator=ModelPolicyEvaluator(provider),
        fail_mode=fail_mode,
        model_enabled=model_enabled,
    )


def test_rules_allow_without_model_when_disabled():
    engine = build_engine(StubProvider(label="OUT_OF_DOMAIN"), model_enabled=False)
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.ALLOW
    assert decision.evaluator == "rules"


def test_model_can_tighten_allowed_decision():
    engine = build_engine(StubProvider(label="OUT_OF_DOMAIN", confidence=0.95))
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.REFUSE
    assert decision.evaluator == "model"


def test_model_never_loosens_rules_refusal():
    engine = build_engine(StubProvider(label="IN_DOMAIN", confidence=0.99))
    decision = engine.evaluate_request(spec=SPEC, text="Who won the World Cup?", has_knowledge=True)
    assert decision.decision == Decision.REFUSE
    assert decision.reason_code == ReasonCode.OUT_OF_DOMAIN
    assert decision.evaluator == "rules"


def test_low_confidence_model_allow_becomes_clarification():
    engine = build_engine(StubProvider(label="IN_DOMAIN", confidence=0.3))
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.CLARIFY


def test_model_failure_fails_closed_by_default():
    engine = build_engine(StubProvider(raises=True))
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.REFUSE
    assert decision.reason_code == ReasonCode.EVALUATOR_UNAVAILABLE
    assert decision.metadata["fail_mode"] == "closed"


def test_model_failure_degrades_to_rules_when_fail_mode_open():
    engine = build_engine(StubProvider(raises=True), fail_mode="open")
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.ALLOW
    assert decision.metadata["degraded"] is True
    assert decision.metadata["degrade_reason"] == "model_evaluator_error"


def test_unrecognised_model_label_degrades_to_review():
    engine = build_engine(StubProvider(label="BANANA", confidence=0.9))
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    assert decision.decision == Decision.REVIEW


def test_response_evaluation_uses_model_when_hybrid():
    engine = build_engine(StubProvider(label="VIOLATION", confidence=0.95))
    decision = engine.evaluate_response(
        spec=SPEC, text="Check-in is at 3 PM.", context_text="Check-in time is 3:00 PM."
    )
    assert decision.decision == Decision.REFUSE
    assert decision.reason_code == ReasonCode.RESPONSE_POLICY_VIOLATION


def test_engine_handles_rules_exception(monkeypatch):
    engine = PolicyEngine(model_enabled=False)

    def boom(**kwargs):
        raise RuntimeError("rules exploded")

    monkeypatch.setattr(engine.rules, "evaluate_request", boom)
    decision = engine.evaluate_request(spec=SPEC, text="hello", has_knowledge=True)
    assert decision.decision == Decision.REFUSE
    assert decision.reason_code == ReasonCode.EVALUATOR_UNAVAILABLE


def test_decision_serialisation_hides_internal_metadata():
    engine = PolicyEngine(model_enabled=False)
    decision = engine.evaluate_request(spec=SPEC, text="What time is check-in?", has_knowledge=True)
    payload = decision.to_api(policy_version=4)
    assert payload["decision"] == "ALLOW"
    assert payload["policy_version"] == 4
    assert "topic_scores" not in payload

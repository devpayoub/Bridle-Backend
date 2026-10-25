"""Deterministic, explainable policy evaluator.

Every branch is testable and produces a stable ``reason_code``. Thresholds come
from the policy configuration, so false-acceptance / false-rejection trade-offs
can be tuned per assistant and measured against the evaluation dataset.
"""
from __future__ import annotations

import re

from django.conf import settings

from apps.policies.engine.matcher import content_token_count, extract_numbers, score_topics
from apps.policies.engine.platform import detect_prompt_injection
from apps.policies.engine.types import Decision, PolicyDecision, PolicySpec, ReasonCode

EVALUATOR_NAME = "rules"
EVALUATOR_VERSION = "rules-1.1.0"

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{7,}\d)(?!\d)")

DEFAULT_MESSAGES = {
    ReasonCode.ALLOWED: "",
    ReasonCode.OUT_OF_DOMAIN: "I can only help with questions about our services.",
    ReasonCode.PROHIBITED_TOPIC: "I'm not able to help with that topic.",
    ReasonCode.PROMPT_INJECTION_DETECTED: "I can't follow instructions like that. Please ask a question about our services.",
    ReasonCode.AMBIGUOUS_REQUEST: "Could you tell me a little more about what you need?",
    ReasonCode.ESCALATION_REQUIRED: "Let me connect you with a member of our team who can help with this.",
    ReasonCode.INSUFFICIENT_KNOWLEDGE: "I don't have approved information about that yet. Let me connect you with our team.",
    ReasonCode.UNSUPPORTED_CLAIM: "I don't have enough approved information to answer that reliably.",
    ReasonCode.RESPONSE_POLICY_VIOLATION: "I'm not able to answer that here.",
    ReasonCode.PII_DETECTED: "I can't share personal information in this conversation.",
    ReasonCode.INVALID_INPUT: "I didn't catch a question. Could you try again?",
    ReasonCode.EVALUATOR_UNAVAILABLE: "This request can't be checked right now. Please try again shortly.",
}


def user_message(spec: PolicySpec, reason: ReasonCode, *, default: str | None = None) -> str:
    """Resolve a customer-overridable message, falling back to a safe default."""
    messages = (spec.response_constraints or {}).get("messages") or {}
    override = messages.get(reason.value)
    if isinstance(override, str) and override.strip():
        return override.strip()[:500]
    if default:
        return default
    if reason in (ReasonCode.OUT_OF_DOMAIN, ReasonCode.PROHIBITED_TOPIC):
        return spec.fallback_message or DEFAULT_MESSAGES[reason]
    return DEFAULT_MESSAGES[reason]


class RulesPolicyEvaluator:
    name = EVALUATOR_NAME
    version = EVALUATOR_VERSION

    # -- request ---------------------------------------------------------
    def evaluate_request(
        self,
        *,
        spec: PolicySpec,
        text: str,
        has_knowledge: bool = False,
    ) -> PolicyDecision:
        cleaned = (text or "").strip()

        if not cleaned:
            return self._decision(
                Decision.REFUSE, ReasonCode.INVALID_INPUT, spec, metadata={"check": "empty_input"}
            )
        if len(cleaned) > settings.POLICY_MAX_INPUT_CHARS:
            return self._decision(
                Decision.REFUSE,
                ReasonCode.INVALID_INPUT,
                spec,
                metadata={"check": "input_too_long", "length": len(cleaned)},
            )

        injection = detect_prompt_injection(cleaned)
        if injection:
            return self._decision(
                Decision.REFUSE,
                ReasonCode.PROMPT_INJECTION_DETECTED,
                spec,
                confidence=0.95,
                metadata={"check": "platform_injection", "pattern": injection},
            )

        if spec.prohibited_topics:
            score, topic, _ = score_topics(cleaned, spec.prohibited_topics)
            if score >= spec.allow_threshold:
                return self._decision(
                    Decision.REFUSE,
                    ReasonCode.PROHIBITED_TOPIC,
                    spec,
                    confidence=score,
                    metadata={"check": "prohibited_topic", "matched_topic": topic, "score": score},
                )

        if spec.escalation_keywords:
            lowered = cleaned.lower()
            for keyword in spec.escalation_keywords:
                if keyword and keyword.lower() in lowered:
                    return self._decision(
                        Decision.ESCALATE,
                        ReasonCode.ESCALATION_REQUIRED,
                        spec,
                        confidence=0.9,
                        metadata={"check": "escalation_keyword", "keyword": keyword},
                    )

        if spec.allowed_topics:
            score, topic, all_scores = score_topics(cleaned, spec.allowed_topics)
            metadata = {
                "check": "domain_match",
                "matched_topic": topic,
                "score": score,
                "allow_threshold": spec.allow_threshold,
                "clarify_threshold": spec.clarify_threshold,
                "topic_scores": dict(sorted(all_scores.items(), key=lambda kv: -kv[1])[:5]),
            }
            if score >= spec.allow_threshold:
                return self._allow_or_gate(spec, has_knowledge, confidence=score, metadata=metadata)
            if score >= spec.clarify_threshold:
                return self._decision(
                    Decision.CLARIFY, ReasonCode.AMBIGUOUS_REQUEST, spec, confidence=1 - score, metadata=metadata
                )
            return self._decision(
                Decision.REFUSE, ReasonCode.OUT_OF_DOMAIN, spec, confidence=1 - score, metadata=metadata
            )

        # No topics configured: still require a minimally substantive question.
        if content_token_count(cleaned) < 2:
            return self._decision(
                Decision.CLARIFY,
                ReasonCode.AMBIGUOUS_REQUEST,
                spec,
                metadata={"check": "too_few_tokens"},
            )
        return self._allow_or_gate(
            spec, has_knowledge, confidence=0.5, metadata={"check": "no_topics_configured"}
        )

    # -- response --------------------------------------------------------
    def evaluate_response(self, *, spec: PolicySpec, text: str, context_text: str = "") -> PolicyDecision:
        cleaned = (text or "").strip()
        if not cleaned:
            return self._decision(
                Decision.REFUSE,
                ReasonCode.RESPONSE_POLICY_VIOLATION,
                spec,
                metadata={"check": "empty_response"},
            )

        injection = detect_prompt_injection(cleaned)
        if injection:
            return self._decision(
                Decision.REFUSE,
                ReasonCode.RESPONSE_POLICY_VIOLATION,
                spec,
                confidence=0.9,
                metadata={"check": "injection_in_response", "pattern": injection},
            )

        if spec.prohibited_topics:
            score, topic, _ = score_topics(cleaned, spec.prohibited_topics)
            if score >= spec.allow_threshold:
                return self._decision(
                    Decision.REFUSE,
                    ReasonCode.RESPONSE_POLICY_VIOLATION,
                    spec,
                    confidence=score,
                    metadata={"check": "prohibited_topic_in_response", "matched_topic": topic, "score": score},
                )

        constraints = spec.response_constraints or {}
        banned = [phrase for phrase in constraints.get("banned_phrases", []) if phrase]
        lowered = cleaned.lower()
        for phrase in banned:
            if phrase.lower() in lowered:
                return self._decision(
                    Decision.REFUSE,
                    ReasonCode.RESPONSE_POLICY_VIOLATION,
                    spec,
                    confidence=0.9,
                    metadata={"check": "banned_phrase", "phrase": phrase[:60]},
                )

        if constraints.get("block_pii"):
            if _EMAIL.search(cleaned) or _PHONE.search(cleaned):
                return self._decision(
                    Decision.REFUSE,
                    ReasonCode.PII_DETECTED,
                    spec,
                    confidence=0.85,
                    metadata={"check": "pii_in_response"},
                )

        if spec.knowledge_required and context_text:
            numbers = extract_numbers(cleaned)
            if numbers:
                supported = numbers & extract_numbers(context_text)
                unsupported = numbers - supported
                ratio = len(unsupported) / len(numbers)
                if ratio > 0.34:
                    return self._decision(
                        Decision.REVIEW,
                        ReasonCode.UNSUPPORTED_CLAIM,
                        spec,
                        confidence=round(ratio, 4),
                        metadata={
                            "check": "grounding",
                            "unsupported_values": sorted(unsupported)[:10],
                            "ratio": round(ratio, 4),
                        },
                    )

        return self._decision(Decision.ALLOW, ReasonCode.ALLOWED, spec, confidence=0.9, metadata={"check": "ok"})

    # -- helpers ---------------------------------------------------------
    def _allow_or_gate(
        self,
        spec: PolicySpec,
        has_knowledge: bool,
        *,
        confidence: float,
        metadata: dict,
    ) -> PolicyDecision:
        if spec.knowledge_required and not has_knowledge:
            decision = (
                Decision.REFUSE if spec.uncertainty_handling == "REFUSE" else Decision.CLARIFY
            )
            return self._decision(
                decision,
                ReasonCode.INSUFFICIENT_KNOWLEDGE,
                spec,
                confidence=confidence,
                metadata={**metadata, "check": "knowledge_required"},
            )
        return self._decision(Decision.ALLOW, ReasonCode.ALLOWED, spec, confidence=confidence, metadata=metadata)

    def _decision(
        self,
        decision: Decision,
        reason: ReasonCode,
        spec: PolicySpec,
        *,
        confidence: float | None = None,
        metadata: dict | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision=decision,
            reason_code=reason,
            user_safe_message=user_message(spec, reason),
            evaluator=self.name,
            evaluator_version=self.version,
            confidence=confidence,
            metadata=metadata or {},
        )

"""Policy configuration services and evaluation persistence."""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from apps.common.exceptions import APIError
from apps.policies.engine import PolicyDecision, PolicyEngine, PolicySpec, get_policy_engine
from apps.policies.models import AssistantPolicy, PolicyEvaluation

logger = logging.getLogger("apps.policies")

_POLICY_FIELDS = (
    "industry",
    "response_constraints",
    "escalation_rules",
    "tool_permissions",
    "uncertainty_handling",
    "evaluator_mode",
    "allow_threshold",
    "clarify_threshold",
)

_TOPIC_FIELDS = ("allowed_topics", "prohibited_topics")


def ensure_policy(assistant) -> AssistantPolicy:
    policy, _created = AssistantPolicy.objects.get_or_create(
        assistant=assistant,
        defaults={
            "industry": assistant.domain_type,
            "allow_threshold": settings.POLICY_DEFAULT_ALLOW_THRESHOLD,
            "clarify_threshold": settings.POLICY_DEFAULT_CLARIFY_THRESHOLD,
        },
    )
    return policy


def get_policy_spec(assistant) -> PolicySpec:
    policy = getattr(assistant, "policy", None)
    if policy is None:
        policy = ensure_policy(assistant)
    return PolicySpec.from_assistant(assistant, policy)


def serialize_policy(assistant) -> dict:
    policy = ensure_policy(assistant)
    return {
        "assistant_id": str(assistant.id),
        "allowed_topics": list(assistant.allowed_topics or []),
        "prohibited_topics": list(assistant.prohibited_topics or []),
        "industry": policy.industry,
        "response_constraints": policy.response_constraints,
        "escalation_rules": policy.escalation_rules,
        "tool_permissions": policy.tool_permissions,
        "uncertainty_handling": policy.uncertainty_handling,
        "evaluator_mode": policy.evaluator_mode,
        "allow_threshold": policy.allow_threshold,
        "clarify_threshold": policy.clarify_threshold,
        "version": policy.version,
        "updated_at": policy.updated_at,
    }


@transaction.atomic
def update_policy(*, assistant, data: dict, actor=None) -> AssistantPolicy:
    if assistant.status == assistant.Status.ARCHIVED:
        raise APIError("Archived assistants cannot be modified.", code="assistant_archived")

    policy = ensure_policy(assistant)
    assistant_changed = False

    for field in _TOPIC_FIELDS:
        if field in data and data[field] != getattr(assistant, field):
            setattr(assistant, field, data[field])
            assistant_changed = True

    policy_changed = False
    for field in _POLICY_FIELDS:
        if field in data and data[field] != getattr(policy, field):
            setattr(policy, field, data[field])
            policy_changed = True

    allow = data.get("allow_threshold", policy.allow_threshold)
    clarify = data.get("clarify_threshold", policy.clarify_threshold)
    if clarify > allow:
        raise APIError(
            "clarify_threshold must be less than or equal to allow_threshold.",
            code="invalid_thresholds",
            extra={"allow_threshold": allow, "clarify_threshold": clarify},
        )

    if not (assistant_changed or policy_changed):
        return policy

    if assistant_changed:
        assistant.save()
    if policy_changed:
        policy.version = policy.version + 1
        policy.updated_by = actor if getattr(actor, "pk", None) else None
        policy.save()

    from apps.assistants.services import create_configuration_version

    create_configuration_version(assistant, actor=actor, reason="policy_updated")

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.POLICY_UPDATED,
        organization=assistant.organization,
        actor_user=actor,
        target_type="assistant",
        target_id=assistant.id,
        metadata={"version": policy.version, "changes": [f for f in _POLICY_FIELDS if f in data]},
    )
    logger.info("Policy updated assistant=%s version=%s", assistant.id, policy.version)
    return policy


def record_evaluation(
    *,
    organization,
    assistant=None,
    phase: str,
    decision: PolicyDecision,
    conversation=None,
    message=None,
    latency_ms: int = 0,
    policy_version: int | None = None,
) -> PolicyEvaluation:
    resolved_version = (
        policy_version if policy_version is not None else int(decision.metadata.get("policy_version", 0) or 0)
    )
    return PolicyEvaluation.objects.create(
        organization=organization,
        assistant=assistant,
        conversation=conversation,
        message=message,
        phase=phase,
        decision=decision.decision.value,
        reason_code=decision.reason_code.value,
        confidence=decision.confidence,
        evaluator=decision.evaluator,
        evaluator_version=decision.evaluator_version,
        policy_version=resolved_version,
        metadata=decision.metadata,
        latency_ms=latency_ms,
    )


def evaluate_request(
    *,
    spec: PolicySpec,
    text: str,
    has_knowledge: bool = False,
    engine: PolicyEngine | None = None,
) -> PolicyDecision:
    engine = engine or get_policy_engine()
    return engine.evaluate_request(spec=spec, text=text, has_knowledge=has_knowledge)


def evaluate_response(
    *, spec: PolicySpec, text: str, context_text: str = "", engine: PolicyEngine | None = None
) -> PolicyDecision:
    engine = engine or get_policy_engine()
    return engine.evaluate_response(spec=spec, text=text, context_text=context_text)


def run_evaluation_set(*, assistant, cases: list[dict]) -> dict:
    """Run a batch of labelled cases through the engine and report accuracy.

    False acceptance and false rejection are reported separately: a single
    accuracy number hides the failure mode that matters most for this product.
    """
    spec = get_policy_spec(assistant)
    engine = get_policy_engine()

    results: list[dict] = []
    correct = 0
    false_acceptance = 0
    false_rejection = 0

    for case in cases:
        decision = engine.evaluate_request(
            spec=spec,
            text=case["input"],
            has_knowledge=case.get("has_knowledge", True),
        )
        expected = case["expected_decision"]
        actual = decision.decision.value
        passed = expected == actual
        if passed:
            correct += 1
        elif actual == "ALLOW":
            false_acceptance += 1
        elif expected == "ALLOW":
            false_rejection += 1

        results.append(
            {
                "input": case["input"][:500],
                "expected_decision": expected,
                "actual_decision": actual,
                "reason_code": decision.reason_code.value,
                "confidence": round(decision.confidence, 4) if decision.confidence is not None else None,
                "passed": passed,
                "note": case.get("note", ""),
            }
        )

    total = len(cases)
    return {
        "assistant_id": str(assistant.id),
        "policy_version": spec.policy_version,
        "evaluator_version": engine.rules.version,
        "summary": {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "false_acceptance": false_acceptance,
            "false_rejection": false_rejection,
            "false_acceptance_rate": round(false_acceptance / total, 4) if total else 0.0,
            "false_rejection_rate": round(false_rejection / total, 4) if total else 0.0,
        },
        "results": results,
        "disclaimer": (
            "A finite labelled set measures this configuration only. Passing it does not prove "
            "complete domain restriction or safety."
        ),
    }

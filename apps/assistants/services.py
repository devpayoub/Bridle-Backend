"""Assistant domain services: lifecycle, configuration snapshots, duplication."""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils.text import slugify

from apps.assistants.models import Assistant, AssistantConfigurationVersion
from apps.common.exceptions import APIError

logger = logging.getLogger("apps.assistants")

_CONFIG_FIELDS = (
    "name",
    "description",
    "domain_type",
    "allowed_topics",
    "prohibited_topics",
    "system_instructions",
    "fallback_message",
    "default_language",
    "model_provider",
    "model_name",
    "generation_parameters",
    "maximum_output_tokens",
    "knowledge_required",
)


def unique_slug(organization, name: str, *, exclude_id=None) -> str:
    base = slugify(name)[:140] or "assistant"
    candidate = base
    suffix = 2
    queryset = Assistant.objects.filter(organization=organization)
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)
    while queryset.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def build_snapshot(assistant: Assistant) -> dict:
    """Full, self-contained configuration snapshot for audit and replay."""
    policy = getattr(assistant, "policy", None)
    snapshot = {
        "assistant": {
            "id": str(assistant.id),
            "name": assistant.name,
            "slug": assistant.slug,
            "description": assistant.description,
            "status": assistant.status,
            "domain_type": assistant.domain_type,
            "allowed_topics": list(assistant.allowed_topics or []),
            "prohibited_topics": list(assistant.prohibited_topics or []),
            "system_instructions": assistant.system_instructions,
            "fallback_message": assistant.fallback_message,
            "default_language": assistant.default_language,
            "model_provider": assistant.model_provider,
            "model_name": assistant.model_name,
            "generation_parameters": assistant.generation_parameters,
            "maximum_output_tokens": assistant.maximum_output_tokens,
            "knowledge_required": assistant.knowledge_required,
        },
        "policy": {
            "industry": getattr(policy, "industry", "") or assistant.domain_type,
            "response_constraints": getattr(policy, "response_constraints", {}) or {},
            "escalation_rules": getattr(policy, "escalation_rules", {}) or {},
            "tool_permissions": getattr(policy, "tool_permissions", {}) or {},
            "uncertainty_handling": getattr(policy, "uncertainty_handling", "CLARIFY"),
            "evaluator_mode": getattr(policy, "evaluator_mode", "RULES"),
            "allow_threshold": getattr(policy, "allow_threshold", None),
            "clarify_threshold": getattr(policy, "clarify_threshold", None),
            "version": getattr(policy, "version", 0),
        },
        "knowledge_base_ids": [str(pk) for pk in assistant.knowledge_bases.values_list("id", flat=True)],
    }
    return snapshot


def create_configuration_version(
    assistant: Assistant, *, actor=None, reason: str = ""
) -> AssistantConfigurationVersion:
    """Persist an immutable snapshot and bump ``assistant.config_version``."""
    next_version = (assistant.config_version or 0) + 1
    record = AssistantConfigurationVersion.objects.create(
        assistant=assistant,
        version=next_version,
        snapshot=build_snapshot(assistant),
        change_reason=reason[:200],
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    assistant.config_version = next_version
    assistant.save(update_fields=["config_version", "updated_at"])
    return record


def validate_ready_for_activation(assistant: Assistant) -> None:
    issues: list[str] = []
    if not (assistant.allowed_topics or []):
        issues.append("At least one allowed topic must be configured.")
    if not (assistant.system_instructions or "").strip():
        issues.append("System instructions must be provided.")
    if not (assistant.model_name or "").strip():
        issues.append("A model must be selected.")
    if not (assistant.fallback_message or "").strip():
        issues.append("A fallback message is required.")
    if issues:
        raise APIError(
            "The assistant is not ready to be activated.",
            code="assistant_not_ready",
            extra={"issues": issues},
        )


@transaction.atomic
def create_assistant(*, organization, data: dict, actor=None) -> Assistant:
    from apps.billing.services import assert_can_create_assistant

    active_count = Assistant.objects.filter(organization=organization).exclude(
        status=Assistant.Status.ARCHIVED
    ).count()
    assert_can_create_assistant(organization, current_count=active_count)

    assistant = Assistant.objects.create(
        organization=organization,
        slug=unique_slug(organization, data.get("name", "assistant")),
        created_by=actor if getattr(actor, "pk", None) else None,
        **{key: value for key, value in data.items() if key in _CONFIG_FIELDS},
    )

    knowledge_base_ids = data.get("knowledge_base_ids") or []
    if knowledge_base_ids:
        from apps.knowledge.models import KnowledgeBase

        knowledge_bases = KnowledgeBase.objects.filter(
            organization=organization, id__in=knowledge_base_ids
        )
        if len({str(pk) for pk in knowledge_base_ids}) != knowledge_bases.count():
            raise APIError(
                "One or more knowledge bases do not belong to this organization.",
                code="invalid_knowledge_base",
            )
        assistant.knowledge_bases.set(knowledge_bases)

    assistant.refresh_from_db()

    from apps.policies.services import ensure_policy

    ensure_policy(assistant)
    assistant.refresh_from_db()
    create_configuration_version(assistant, actor=actor, reason="created")

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.ASSISTANT_CREATED,
        organization=organization,
        actor_user=actor,
        target_type="assistant",
        target_id=assistant.id,
        metadata={"name": assistant.name, "status": assistant.status},
    )
    logger.info("Assistant created id=%s org=%s", assistant.id, organization.id)
    return assistant


@transaction.atomic
def update_assistant(assistant: Assistant, *, data: dict, actor=None, reason: str = "updated") -> Assistant:
    if assistant.status == Assistant.Status.ARCHIVED:
        raise APIError("Archived assistants cannot be modified.", code="assistant_archived")

    changed: list[str] = []
    for field in _CONFIG_FIELDS:
        if field in data:
            value = data[field]
            if getattr(assistant, field) != value:
                setattr(assistant, field, value)
                changed.append(field)

    if "knowledge_base_ids" in data:
        from apps.knowledge.models import KnowledgeBase

        ids = data["knowledge_base_ids"] or []
        knowledge_bases = KnowledgeBase.objects.filter(organization=assistant.organization, id__in=ids)
        if len({str(pk) for pk in ids}) != knowledge_bases.count():
            raise APIError("One or more knowledge bases do not belong to this organization.", code="invalid_knowledge_base")
        assistant.knowledge_bases.set(knowledge_bases)
        changed.append("knowledge_bases")

    if "name" in data and data["name"] != assistant.name:
        assistant.slug = unique_slug(assistant.organization, data["name"], exclude_id=assistant.id)

    if not changed:
        return assistant

    assistant.save()
    create_configuration_version(assistant, actor=actor, reason=reason)

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.ASSISTANT_UPDATED,
        organization=assistant.organization,
        actor_user=actor,
        target_type="assistant",
        target_id=assistant.id,
        metadata={"changes": changed, "version": assistant.config_version},
    )
    return assistant


_ALLOWED_TRANSITIONS = {
    Assistant.Status.DRAFT: {Assistant.Status.ACTIVE, Assistant.Status.ARCHIVED},
    Assistant.Status.ACTIVE: {Assistant.Status.PAUSED, Assistant.Status.ARCHIVED},
    Assistant.Status.PAUSED: {Assistant.Status.ACTIVE, Assistant.Status.ARCHIVED},
    Assistant.Status.ARCHIVED: set(),
}


@transaction.atomic
def change_status(assistant: Assistant, target: str, *, actor=None) -> Assistant:
    if target not in Assistant.Status.values:
        raise APIError("Unknown assistant status.", code="invalid_status")

    current = assistant.status
    if current == target:
        return assistant
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise APIError(
            f"Cannot change status from {current} to {target}.",
            code="invalid_status_transition",
            extra={"current": current, "requested": target},
        )
    if target == Assistant.Status.ACTIVE:
        validate_ready_for_activation(assistant)

    assistant.status = target
    assistant.save(update_fields=["status", "updated_at"])

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.ASSISTANT_STATUS_CHANGED,
        organization=assistant.organization,
        actor_user=actor,
        target_type="assistant",
        target_id=assistant.id,
        metadata={"previous_status": current, "status": target},
    )
    logger.info("Assistant status changed id=%s %s->%s", assistant.id, current, target)
    return assistant


@transaction.atomic
def duplicate_assistant(assistant: Assistant, *, actor=None, name: str | None = None) -> Assistant:
    from apps.billing.services import assert_can_create_assistant
    from apps.policies.services import ensure_policy

    active_count = Assistant.objects.filter(organization=assistant.organization).exclude(
        status=Assistant.Status.ARCHIVED
    ).count()
    assert_can_create_assistant(assistant.organization, current_count=active_count)

    copy_name = (name or f"{assistant.name} (copy)")[:150]
    duplicate = Assistant.objects.create(
        organization=assistant.organization,
        name=copy_name,
        slug=unique_slug(assistant.organization, copy_name),
        description=assistant.description,
        status=Assistant.Status.DRAFT,
        domain_type=assistant.domain_type,
        allowed_topics=list(assistant.allowed_topics or []),
        prohibited_topics=list(assistant.prohibited_topics or []),
        system_instructions=assistant.system_instructions,
        fallback_message=assistant.fallback_message,
        default_language=assistant.default_language,
        model_provider=assistant.model_provider,
        model_name=assistant.model_name,
        generation_parameters=dict(assistant.generation_parameters or {}),
        maximum_output_tokens=assistant.maximum_output_tokens,
        knowledge_required=assistant.knowledge_required,
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    duplicate.knowledge_bases.set(assistant.knowledge_bases.all())

    policy = getattr(assistant, "policy", None)
    new_policy = ensure_policy(duplicate)
    if policy is not None:
        for field in (
            "industry",
            "response_constraints",
            "escalation_rules",
            "tool_permissions",
            "uncertainty_handling",
            "evaluator_mode",
            "allow_threshold",
            "clarify_threshold",
        ):
            setattr(new_policy, field, getattr(policy, field))
        new_policy.save()

    create_configuration_version(duplicate, actor=actor, reason=f"duplicated_from:{assistant.id}")

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.ASSISTANT_DUPLICATED,
        organization=assistant.organization,
        actor_user=actor,
        target_type="assistant",
        target_id=duplicate.id,
        metadata={"assistant_id": str(assistant.id), "name": duplicate.name},
    )
    return duplicate


def usage_statistics(assistant: Assistant, *, since=None, until=None) -> dict:
    from django.db.models import Count, Q, Sum

    from apps.usage.models import UsageRecord

    queryset = UsageRecord.objects.filter(assistant=assistant, kind=UsageRecord.Kind.CHAT)
    if since:
        queryset = queryset.filter(created_at__gte=since)
    if until:
        queryset = queryset.filter(created_at__lt=until)

    aggregates = queryset.aggregate(
        total_requests=Count("id"),
        successful=Count("id", filter=Q(outcome=UsageRecord.Outcome.SUCCESS)),
        refused=Count("id", filter=Q(outcome=UsageRecord.Outcome.REFUSED)),
        errors=Count("id", filter=Q(outcome=UsageRecord.Outcome.ERROR)),
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        avg_latency=Sum("latency_ms"),
    )
    total = aggregates["total_requests"] or 0
    return {
        "assistant_id": str(assistant.id),
        "total_requests": total,
        "successful": aggregates["successful"] or 0,
        "refused": aggregates["refused"] or 0,
        "errors": aggregates["errors"] or 0,
        "input_tokens": aggregates["input_tokens"] or 0,
        "output_tokens": aggregates["output_tokens"] or 0,
        "average_latency_ms": round((aggregates["avg_latency"] or 0) / total, 2) if total else 0,
    }


# ---------------------------------------------------------------------------
# AI-assisted drafting
# ---------------------------------------------------------------------------
_DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (Assistant.DomainType.HOTEL, ("hotel", "resort", "hospitality", "guest", "concierge", "front desk", "restaurant")),
    (Assistant.DomainType.HOSPITAL, ("hospital", "clinic", "patient", "medical", "health", "doctor", "nurse")),
    (Assistant.DomainType.ECOMMERCE, ("ecommerce", "e-commerce", "shop", "store", "retail", "product", "cart", "order")),
    (Assistant.DomainType.BOOKING, ("booking", "reservation", "appointment", "schedule", "travel", "flight", "ticket")),
    (Assistant.DomainType.UNIVERSITY, ("university", "school", "campus", "student", "course", "admission")),
)

_DRAFT_SYSTEM_PROMPT = """You configure a domain-restricted AI assistant.
Given a description of the business, reply with ONLY compact JSON:
{"name": "<short assistant name, max 60 chars>",
 "description": "<one sentence>",
 "domain_type": "HOTEL" | "HOSPITAL" | "ECOMMERCE" | "BOOKING" | "UNIVERSITY" | "GENERIC" | "OTHER",
 "allowed_topics": ["<5-8 short topics the assistant may answer>"],
 "prohibited_topics": ["<3-6 short topics it must refuse>"],
 "system_instructions": "<what the assistant is and how it behaves, max 1200 chars>",
 "fallback_message": "<what to say when a request is out of scope, max 200 chars>"}
Topics are short noun phrases, lowercase, no punctuation."""


def _first_json_object(raw: str) -> dict:
    import json
    import re

    if not raw:
        return {}
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _guess_domain(description: str) -> str:
    lowered = description.lower()
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return domain
    return Assistant.DomainType.GENERIC


def _clean_topics(value, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        topic = item.strip().strip(".").lower()[:80]
        if topic and topic not in seen:
            seen.append(topic)
        if len(seen) >= limit:
            break
    return seen


_STOPWORDS = {
    "a", "an", "the", "of", "for", "with", "in", "on", "and", "or", "to", "our", "my",
    "we", "is", "are", "that", "this", "about", "focusing", "focused",
}


def _template_draft(description: str) -> dict:
    """Deterministic draft used when no model is available or its reply is unusable."""
    import re

    domain = _guess_domain(description)
    words = [w for w in re.findall(r"[A-Za-z0-9]+", description) if w.lower() not in _STOPWORDS][:3]
    name = " ".join(words).title()[:60] or "New assistant"
    subject = description.strip().rstrip(".")[:80] or "this business"

    return {
        "name": name,
        "description": description.strip()[:300],
        "domain_type": domain,
        "allowed_topics": ["opening hours", "location and contact", "services offered", "pricing", "policies"],
        "prohibited_topics": ["legal advice", "medical diagnosis", "price negotiation"],
        "system_instructions": (
            f"You are the support assistant for {subject}. "
            "Answer only questions about the topics listed in the policy. "
            "Use the approved knowledge sources, never invent details, and hand over to a human when unsure."
        ),
        "fallback_message": f"I can only help with questions about {subject}.",
    }


def draft_assistant(*, organization, description: str) -> dict:
    """Turn a plain-language domain description into a draft assistant configuration.

    Calls the workspace's configured provider when one is available; falls back to
    a deterministic template so the feature still works on the mock provider and
    when a model replies with something unparsable. The response says which path
    produced the draft, so the console never implies AI involvement it did not get.
    """
    from apps.ai_gateway.base import GenerationRequest, ProviderError, ProviderMessage
    from apps.organizations.services import resolve_provider

    fallback = _template_draft(description)
    try:
        provider, model, source = resolve_provider(organization)
    except Exception:  # noqa: BLE001 - a broken credential must not break drafting
        return {**fallback, "provider": "", "model": "", "source": "template", "generated": False}

    if provider.name == "mock":
        # The mock provider returns fixed prose; a template is more useful and honest.
        return {**fallback, "provider": provider.name, "model": model, "source": "template", "generated": False}

    try:
        result = provider.generate_response(
            GenerationRequest(
                model=model,
                messages=[
                    ProviderMessage(role="system", content=_DRAFT_SYSTEM_PROMPT),
                    ProviderMessage(role="user", content=description[:2000]),
                ],
                max_output_tokens=700,
                temperature=0.2,
            )
        )
    except ProviderError:
        return {**fallback, "provider": provider.name, "model": model, "source": "template", "generated": False}

    parsed = _first_json_object(result.text)
    if not parsed:
        return {**fallback, "provider": provider.name, "model": model, "source": "template", "generated": False}

    valid_domains = {choice for choice, _ in Assistant.DomainType.choices}
    domain = str(parsed.get("domain_type", "")).upper()
    draft = {
        "name": str(parsed.get("name") or fallback["name"])[:60],
        "description": str(parsed.get("description") or fallback["description"])[:300],
        "domain_type": domain if domain in valid_domains else fallback["domain_type"],
        "allowed_topics": _clean_topics(parsed.get("allowed_topics")) or fallback["allowed_topics"],
        "prohibited_topics": _clean_topics(parsed.get("prohibited_topics"), limit=6)
        or fallback["prohibited_topics"],
        "system_instructions": str(parsed.get("system_instructions") or fallback["system_instructions"])[:1200],
        "fallback_message": str(parsed.get("fallback_message") or fallback["fallback_message"])[:200],
    }
    return {**draft, "provider": provider.name, "model": result.model or model, "source": "model", "generated": True}

"""Chat orchestration.

Ordering and failure semantics
------------------------------
1. Authenticate + tenant/assistant authorisation (caller does this).
2. Rate limit, then idempotency claim.
3. Deterministic policy check. A conclusive refusal **never** reaches the model.
4. Knowledge retrieval (tenant scoped), then the knowledge gate.
5. Quota reservation, provider call (outside any transaction).
6. Response validation; an unvalidated answer is never returned.
7. One short transaction persists the interaction.

If the provider fails, the user message is stored and the request returns 502.
No assistant message is fabricated, so history never implies an answer that was
not produced.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.ai_gateway.base import GenerationRequest, ProviderError
from apps.ai_gateway.registry import get_provider
from apps.assistants.models import Assistant
from apps.common.exceptions import (
    APIError,
    AssistantNotAvailable,
    IdempotencyConflict,
    ProviderUnavailable,
    TenantAccessDenied,
)
from apps.conversations.models import Conversation, IdempotencyRecord, Message
from apps.conversations.prompting import build_messages, format_context, generation_parameters
from apps.knowledge.models import RetrievalRecord
from apps.knowledge.retrievers import retrieve_for_assistant
from apps.policies.engine import Decision, PolicyDecision, PolicySpec, ReasonCode, get_policy_engine
from apps.policies.services import get_policy_spec, record_evaluation
from apps.usage.models import UsageRecord
from apps.usage.quota import METRIC_CHAT, reserve_quota
from apps.usage.rate_limit import enforce_rate_limit

logger = logging.getLogger("apps.conversations.chat")

_OUTCOME_BY_DECISION = {
    Decision.ALLOW: UsageRecord.Outcome.SUCCESS,
    Decision.REFUSE: UsageRecord.Outcome.REFUSED,
    Decision.CLARIFY: UsageRecord.Outcome.CLARIFICATION,
    Decision.ESCALATE: UsageRecord.Outcome.ESCALATED,
    Decision.REVIEW: UsageRecord.Outcome.REFUSED,
}


@dataclass
class ChatOutcome:
    payload: dict
    http_status: int = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_idempotency(organization, key: str, fingerprint: str) -> tuple[IdempotencyRecord | None, dict | None]:
    """Return ``(record, cached_response)``.

    ``cached_response`` is set when the same key+fingerprint already completed.
    """
    if not key:
        return None, None

    expires_at = timezone.now() + timedelta(hours=settings.CHAT_IDEMPOTENCY_TTL_HOURS)
    record, created = IdempotencyRecord.objects.get_or_create(
        organization=organization,
        key=key[:128],
        defaults={"request_fingerprint": fingerprint, "response_status": 0, "response_body": {}, "expires_at": expires_at},
    )
    if created:
        return record, None

    if record.request_fingerprint != fingerprint:
        raise IdempotencyConflict()
    if record.response_status == 0:
        raise APIError(
            "A request with this Idempotency-Key is already in progress.",
            code="idempotency_in_progress",
            extra=None,
        )
    return record, record.response_body


def _release_idempotency(record: IdempotencyRecord | None) -> None:
    if record is None:
        return
    try:
        IdempotencyRecord.objects.filter(pk=record.pk, response_status=0).delete()
    except Exception:  # pragma: no cover - cleanup must not mask the original error
        logger.warning("Unable to release idempotency record", exc_info=True)


def _apply_response_constraints(spec: PolicySpec, text: str) -> tuple[str, bool]:
    """Truncate to the configured maximum length at a word boundary."""
    max_chars = (spec.response_constraints or {}).get("max_chars")
    try:
        max_chars = int(max_chars) if max_chars else None
    except (TypeError, ValueError):
        max_chars = None
    if not max_chars or len(text) <= max_chars:
        return text, False
    truncated = text[:max_chars]
    cut = truncated.rfind(" ")
    if cut > int(max_chars * 0.6):
        truncated = truncated[:cut]
    return truncated.rstrip() + "…", True


def _load_conversation(organization, assistant: Assistant, conversation_id) -> Conversation:
    conversation = Conversation.objects.filter(
        id=conversation_id, organization=organization, assistant=assistant
    ).first()
    if conversation is None:
        raise TenantAccessDenied("Conversation not found.")
    if conversation.status == Conversation.Status.ANONYMIZED:
        raise APIError("This conversation has been anonymised.", code="conversation_closed")
    return conversation


def _history(conversation: Conversation) -> list[tuple[str, str]]:
    rows = list(
        conversation.messages.filter(
            status=Message.Status.COMPLETED, role__in=[Message.Role.USER, Message.Role.ASSISTANT]
        )
        .order_by("-created_at")
        .values_list("role", "content")[: settings.CHAT_HISTORY_MAX_MESSAGES]
    )
    rows.reverse()
    return [(role.lower(), content) for role, content in rows]


def _build_payload(
    *,
    conversation: Conversation,
    assistant: Assistant,
    message: Message,
    decision: PolicyDecision,
    spec: PolicySpec,
    content: str,
    citations: list[dict],
    usage: dict,
    latency_ms: int,
    request_id: str,
) -> dict:
    return {
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "assistant_id": str(assistant.id),
        "decision": decision.decision.value,
        "reason_code": decision.reason_code.value,
        "content": content,
        "citations": citations,
        "usage": usage,
        "policy": {
            "decision": decision.decision.value,
            "reason_code": decision.reason_code.value,
            "confidence": round(decision.confidence, 4) if decision.confidence is not None else None,
            "policy_version": spec.policy_version,
            "evaluator": decision.evaluator,
        },
        "latency_ms": latency_ms,
        "request_id": request_id,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def handle_chat_request(
    *,
    tenant,
    assistant_id,
    message: str,
    conversation_id=None,
    metadata: dict | None = None,
    idempotency_key: str = "",
    request_id: str = "",
    provider=None,
    engine=None,
) -> ChatOutcome:
    organization = tenant.organization
    started = time.monotonic()

    assistant = (
        Assistant.objects.select_related("policy")
        .filter(id=assistant_id, organization=organization)
        .first()
    )
    if assistant is None:
        raise TenantAccessDenied("Assistant not found.")

    api_key = tenant.api_key
    if api_key is not None and api_key.assistant_id and str(api_key.assistant_id) != str(assistant.id):
        raise TenantAccessDenied("Assistant not found.")

    if not assistant.accepts_requests:
        raise AssistantNotAvailable(
            "This assistant is not accepting requests.",
            extra={"status": assistant.status},
        )

    text = (message or "").strip()
    if not text:
        raise APIError("A message is required.", code="empty_message")
    if len(text) > settings.CHAT_MAX_MESSAGE_CHARS:
        raise APIError(
            "The message is too long.",
            code="message_too_long",
            extra={"max_chars": settings.CHAT_MAX_MESSAGE_CHARS, "length": len(text)},
        )

    # Rate limiting happens before any expensive work. Channels pass their own
    # scope so a public bot cannot starve the console's bucket; everything else
    # keeps the "chat" scope exactly as before.
    enforce_rate_limit(getattr(tenant, "rate_limit_scope", None) or "chat", f"org:{organization.id}")
    if api_key is not None:
        enforce_rate_limit("chat", f"key:{api_key.id}")

    metadata = metadata or {}
    fingerprint = _fingerprint(
        {
            "assistant_id": str(assistant.id),
            "conversation_id": str(conversation_id) if conversation_id else None,
            "message": text,
            "metadata": metadata,
        }
    )
    idempotency_record, cached = _claim_idempotency(organization, idempotency_key, fingerprint)
    if cached:
        return ChatOutcome(payload=cached, http_status=200)

    try:
        return _execute(
            tenant=tenant,
            assistant=assistant,
            text=text,
            conversation_id=conversation_id,
            metadata=metadata,
            request_id=request_id,
            started=started,
            provider=provider,
            engine=engine,
            idempotency_record=idempotency_record,
        )
    except Exception:
        _release_idempotency(idempotency_record)
        raise


def _execute(
    *,
    tenant,
    assistant: Assistant,
    text: str,
    conversation_id,
    metadata: dict,
    request_id: str,
    started: float,
    provider,
    engine,
    idempotency_record,
) -> ChatOutcome:
    organization = tenant.organization
    policy = getattr(assistant, "policy", None)
    spec = get_policy_spec(assistant)
    engine = engine or get_policy_engine()

    conversation = (
        _load_conversation(organization, assistant, conversation_id)
        if conversation_id
        else Conversation.objects.create(
            organization=organization,
            assistant=assistant,
            external_user_id=str(metadata.get("external_user_id", ""))[:128],
            config_version=assistant.config_version,
            retention_expires_at=timezone.now() + timedelta(days=settings.CONVERSATION_RETENTION_DAYS),
            metadata={key: metadata[key] for key in list(metadata)[:20]},
        )
    )

    # -- 1. deterministic request screening -----------------------------
    request_decision = engine.evaluate_request(spec=spec, text=text, has_knowledge=True)

    if request_decision.decision != Decision.ALLOW:
        return _finalise(
            conversation=conversation,
            assistant=assistant,
            user_text=text,
            assistant_text=request_decision.user_safe_message,
            request_decision=request_decision,
            response_decision=None,
            spec=spec,
            citations=[],
            chunks=[],
            retrieval_latency_ms=0,
            retriever_name="none",
            usage={"input_tokens": 0, "output_tokens": 0, "estimated": False},
            provider_name="",
            model_name="",
            provider_latency_ms=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_id=request_id,
            outcome=_OUTCOME_BY_DECISION[request_decision.decision],
            message_status=(
                Message.Status.BLOCKED
                if request_decision.decision in {Decision.REFUSE, Decision.REVIEW}
                else Message.Status.COMPLETED
            ),
            idempotency_record=idempotency_record,
            tenant=tenant,
        )

    # -- 2. approved knowledge ------------------------------------------
    chunks, retrieval_latency_ms, retriever_name = retrieve_for_assistant(assistant, text)

    if spec.knowledge_required and not chunks:
        decision = PolicyDecision(
            decision=Decision.REFUSE if spec.uncertainty_handling == "REFUSE" else Decision.CLARIFY,
            reason_code=ReasonCode.INSUFFICIENT_KNOWLEDGE,
            user_safe_message=_message_for(spec, ReasonCode.INSUFFICIENT_KNOWLEDGE),
            evaluator="rules",
            evaluator_version="rules-1.1.0",
            confidence=None,
            metadata={"check": "knowledge_gate", "policy_version": spec.policy_version},
        )
        return _finalise(
            conversation=conversation,
            assistant=assistant,
            user_text=text,
            assistant_text=decision.user_safe_message,
            request_decision=decision,
            response_decision=None,
            spec=spec,
            citations=[],
            chunks=[],
            retrieval_latency_ms=retrieval_latency_ms,
            retriever_name=retriever_name,
            usage={"input_tokens": 0, "output_tokens": 0, "estimated": False},
            provider_name="",
            model_name="",
            provider_latency_ms=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_id=request_id,
            outcome=_OUTCOME_BY_DECISION[decision.decision],
            message_status=Message.Status.BLOCKED,
            idempotency_record=idempotency_record,
            tenant=tenant,
        )

    # -- 3. generation ---------------------------------------------------
    resolved_provider = provider or _resolve_provider(assistant)
    params = generation_parameters(assistant)
    provider_request = GenerationRequest(
        model=assistant.model_name,
        messages=build_messages(
            assistant=assistant,
            spec=spec,
            history=_history(conversation),
            user_message=text,
            chunks=chunks,
        ),
        max_output_tokens=min(assistant.maximum_output_tokens, settings.CHAT_MAX_OUTPUT_TOKENS),
        temperature=params["temperature"],
        top_p=params["top_p"],
        timeout=settings.CHAT_REQUEST_TIMEOUT,
    )

    try:
        with reserve_quota(organization, METRIC_CHAT):
            generation = resolved_provider.generate_response(provider_request)
    except ProviderError as exc:
        logger.warning("Provider failure assistant=%s code=%s", assistant.id, exc.code)
        _persist_failed_turn(conversation=conversation, text=text, tenant=tenant)
        message = (
            "The AI provider account is out of credit. Ask your administrator to top up the provider account."
            if exc.code == "provider_quota_exceeded"
            else "The AI provider could not complete this request. Please retry."
        )
        raise ProviderUnavailable(
            message,
            extra={"provider_code": exc.code, "provider_message": str(exc)[:200]},
        ) from exc

    # -- 4. response validation -----------------------------------------
    context_text = format_context(chunks)
    response_decision = engine.evaluate_response(spec=spec, text=generation.text, context_text=context_text)

    if response_decision.decision == Decision.ALLOW:
        content, truncated = _apply_response_constraints(spec, generation.text)
        citations = [chunk.as_citation() for chunk in chunks]
    else:
        content = response_decision.user_safe_message
        citations = []
        truncated = False

    if truncated:
        response_decision = PolicyDecision(
            decision=response_decision.decision,
            reason_code=response_decision.reason_code,
            user_safe_message=response_decision.user_safe_message,
            evaluator=response_decision.evaluator,
            evaluator_version=response_decision.evaluator_version,
            confidence=response_decision.confidence,
            metadata={**response_decision.metadata, "truncated": True},
        )

    outcome = (
        UsageRecord.Outcome.SUCCESS
        if response_decision.decision == Decision.ALLOW
        else _OUTCOME_BY_DECISION[response_decision.decision]
    )

    return _finalise(
        conversation=conversation,
        assistant=assistant,
        user_text=text,
        assistant_text=content,
        request_decision=request_decision,
        response_decision=response_decision,
        spec=spec,
        citations=citations,
        chunks=chunks,
        retrieval_latency_ms=retrieval_latency_ms,
        retriever_name=retriever_name,
        usage={
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
            "estimated": generation.usage_estimated,
        },
        provider_name=generation.provider,
        model_name=generation.model,
        provider_latency_ms=generation.latency_ms,
        latency_ms=int((time.monotonic() - started) * 1000),
        request_id=request_id,
        outcome=outcome,
        message_status=(
            Message.Status.COMPLETED if response_decision.decision == Decision.ALLOW else Message.Status.BLOCKED
        ),
        idempotency_record=idempotency_record,
        tenant=tenant,
        raw_provider_metadata={
            "finish_reason": generation.finish_reason,
            "provider_request_id": generation.provider_request_id,
            "usage_estimated": generation.usage_estimated,
        },
    )


def _message_for(spec: PolicySpec, reason: ReasonCode) -> str:
    from apps.policies.engine.rules import user_message

    return user_message(spec, reason)


def _workspace_api_key(assistant: Assistant) -> str | None:
    """API key stored on the workspace, if any.

    Falls back to ``None`` so the provider adapter uses the environment key — the
    default for single-tenant installs.
    """
    settings_row = getattr(assistant.organization, "ai_settings", None)
    if settings_row is None or not settings_row.has_api_key:
        return None
    return settings_row.get_api_key() or None


def _resolve_provider(assistant: Assistant):
    try:
        return get_provider(assistant.model_provider, api_key=_workspace_api_key(assistant))
    except ProviderError as exc:
        # A missing credential is the most common misconfiguration: say where to fix it.
        message = (
            "No AI API key is configured for this provider. Ask your administrator to set OPENAI_API_KEY."
            if exc.code == "provider_auth_error"
            else "The assistant's AI provider is not configured correctly."
        )
        raise ProviderUnavailable(message, extra={"provider_code": exc.code}) from exc
    except Exception as exc:  # noqa: BLE001 - misconfiguration must be a 502, not a 500
        raise ProviderUnavailable(
            "The assistant's AI provider is not configured correctly."
        ) from exc


def _persist_failed_turn(*, conversation: Conversation, text: str, tenant) -> None:
    """Store the user message so the transcript stays truthful after a failure."""
    try:
        with transaction.atomic():
            Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
            Conversation.objects.filter(pk=conversation.pk).update(last_message_at=timezone.now())
            UsageRecord.objects.create(
                organization=tenant.organization,
                assistant=conversation.assistant,
                api_key=tenant.api_key,
                conversation=conversation,
                kind=UsageRecord.Kind.CHAT,
                outcome=UsageRecord.Outcome.ERROR,
            )
    except Exception:  # pragma: no cover - best effort
        logger.exception("Unable to persist failed chat turn")


def _finalise(
    *,
    conversation: Conversation,
    assistant: Assistant,
    user_text: str,
    assistant_text: str,
    request_decision: PolicyDecision,
    response_decision: PolicyDecision | None,
    spec: PolicySpec,
    citations: list[dict],
    chunks,
    retrieval_latency_ms: int,
    retriever_name: str,
    usage: dict,
    provider_name: str,
    model_name: str,
    provider_latency_ms: int,
    latency_ms: int,
    request_id: str,
    outcome: str,
    message_status: str,
    idempotency_record,
    tenant,
    raw_provider_metadata: dict | None = None,
) -> ChatOutcome:
    final_decision = response_decision or request_decision

    with transaction.atomic():
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=user_text,
            policy_decision=request_decision.decision.value,
            refusal_reason_code=(
                request_decision.reason_code.value
                if request_decision.decision != Decision.ALLOW
                else ""
            ),
        )
        assistant_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=assistant_text,
            status=message_status,
            policy_decision=final_decision.decision.value,
            refusal_reason_code=(
                final_decision.reason_code.value if final_decision.decision != Decision.ALLOW else ""
            ),
            citations=citations,
            provider_metadata={
                "provider": provider_name,
                "model": model_name,
                "latency_ms": provider_latency_ms,
                **(raw_provider_metadata or {}),
            },
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
        )

        record_evaluation(
            organization=conversation.organization,
            assistant=assistant,
            phase="REQUEST",
            decision=request_decision,
            conversation=conversation,
            message=user_message,
            latency_ms=latency_ms,
            policy_version=spec.policy_version,
        )
        if response_decision is not None:
            record_evaluation(
                organization=conversation.organization,
                assistant=assistant,
                phase="RESPONSE",
                decision=response_decision,
                conversation=conversation,
                message=assistant_message,
                latency_ms=latency_ms,
                policy_version=spec.policy_version,
            )

        if chunks:
            RetrievalRecord.objects.create(
                organization=conversation.organization,
                assistant=assistant,
                conversation_id=conversation.id,
                query_hash=hashlib.sha256(user_text.encode("utf-8")).hexdigest(),
                query_length=len(user_text),
                chunk_ids=[chunk.chunk_id for chunk in chunks],
                scores=[round(chunk.score, 4) for chunk in chunks],
                retriever=retriever_name,
                result_count=len(chunks),
                latency_ms=retrieval_latency_ms,
            )

        UsageRecord.objects.create(
            organization=conversation.organization,
            assistant=assistant,
            api_key=tenant.api_key,
            conversation=conversation,
            kind=UsageRecord.Kind.CHAT,
            outcome=outcome,
            request_id=request_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            token_usage_estimated=usage["estimated"],
            provider=provider_name,
            model=model_name,
            latency_ms=latency_ms,
            provider_latency_ms=provider_latency_ms,
        )

        Conversation.objects.filter(pk=conversation.pk).update(last_message_at=timezone.now())
        if conversation.config_version == 0:
            Conversation.objects.filter(pk=conversation.pk).update(config_version=assistant.config_version)

    payload = _build_payload(
        conversation=conversation,
        assistant=assistant,
        message=assistant_message,
        decision=final_decision,
        spec=spec,
        content=assistant_text,
        citations=citations,
        usage=usage,
        latency_ms=latency_ms,
        request_id=request_id,
    )

    if idempotency_record is not None:
        IdempotencyRecord.objects.filter(pk=idempotency_record.pk).update(
            response_status=200, response_body=payload
        )

    logger.info(
        "chat.completed conversation=%s assistant=%s decision=%s latency_ms=%s",
        conversation.id,
        assistant.id,
        final_decision.decision.value,
        latency_ms,
    )
    return ChatOutcome(payload=payload, http_status=200)

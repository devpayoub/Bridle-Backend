"""Independent policy evaluation endpoints.

Capability B of the product: a developer can evaluate a request (and optionally a
proposed response) against a policy without using the managed assistant flow.
"""
from __future__ import annotations

import time

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistants.models import Assistant
from apps.common.exceptions import TenantAccessDenied
from apps.organizations.context import CAP_POLICY_EVALUATE, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability
from apps.policies.engine import Decision, PolicySpec, get_policy_engine
from apps.policies.serializers import PolicyEvaluationRequestSerializer
from apps.policies.services import get_policy_spec, record_evaluation
from apps.usage.models import UsageRecord
from apps.usage.quota import METRIC_POLICY, reserve_quota
from apps.usage.rate_limit import enforce_rate_limit

_PUBLIC_METADATA_KEYS = {
    "check",
    "matched_topic",
    "score",
    "allow_threshold",
    "clarify_threshold",
    "topic_scores",
    "pattern",
    "keyword",
    "ratio",
    "unsupported_values",
    "truncated",
    "degraded",
    "degrade_reason",
    "fail_mode",
    "phase",
}


def _public_metadata(metadata: dict) -> dict:
    """Expose only explainability fields, never internal scoring internals."""
    public: dict = {}
    for key, value in (metadata or {}).items():
        if key not in _PUBLIC_METADATA_KEYS:
            continue
        if isinstance(value, dict):
            public[key] = {str(k)[:60]: v for k, v in list(value.items())[:10]}
        elif isinstance(value, list):
            public[key] = value[:10]
        else:
            public[key] = value
    return public


class PolicyEvaluationBaseView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, require_capability(CAP_POLICY_EVALUATE)]

    def _resolve_spec(self, tenant, data) -> tuple[PolicySpec, Assistant | None]:
        assistant = None
        if data.get("assistant_id"):
            assistant = Assistant.objects.filter(
                id=data["assistant_id"], organization=tenant.organization
            ).first()
            if assistant is None:
                raise TenantAccessDenied("Assistant not found.")
            return get_policy_spec(assistant), assistant

        inline = data["policy"]
        from django.conf import settings

        spec = PolicySpec(
            allowed_topics=list(inline.get("allowed_topics") or []),
            prohibited_topics=list(inline.get("prohibited_topics") or []),
            escalation_keywords=list((inline.get("escalation_rules") or {}).get("keywords", [])),
            response_constraints=dict(inline.get("response_constraints") or {}),
            knowledge_required=bool(inline.get("knowledge_required", False)),
            uncertainty_handling=inline.get("uncertainty_handling", "CLARIFY"),
            allow_threshold=float(inline.get("allow_threshold", settings.POLICY_DEFAULT_ALLOW_THRESHOLD)),
            clarify_threshold=float(
                inline.get("clarify_threshold", settings.POLICY_DEFAULT_CLARIFY_THRESHOLD)
            ),
            industry=inline.get("industry", ""),
            policy_version=0,
            evaluator_mode=inline.get("evaluator_mode", "RULES"),
        )
        return spec, None


@extend_schema(tags=["Policies"], request=PolicyEvaluationRequestSerializer, responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)}, summary="Evaluate a request")
class PolicyEvaluateRequestView(PolicyEvaluationBaseView):
    def post(self, request):
        tenant = get_tenant(request)
        enforce_rate_limit("policy.evaluate", f"org:{tenant.organization.id}")
        serializer = PolicyEvaluationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        spec, assistant = self._resolve_spec(tenant, data)
        engine = get_policy_engine()

        started = time.monotonic()
        with reserve_quota(tenant.organization, METRIC_POLICY):
            decision = engine.evaluate_request(
                spec=spec, text=data["request_text"], has_knowledge=True
            )
            response_decision = None
            if data.get("proposed_response") and decision.decision == Decision.ALLOW:
                response_decision = engine.evaluate_response(
                    spec=spec,
                    text=data["proposed_response"],
                    context_text=data.get("knowledge_context", ""),
                )
        latency_ms = int((time.monotonic() - started) * 1000)

        record_evaluation(
            organization=tenant.organization,
            assistant=assistant,
            phase="REQUEST",
            decision=decision,
            latency_ms=latency_ms,
            policy_version=spec.policy_version,
        )
        if response_decision is not None:
            record_evaluation(
                organization=tenant.organization,
                assistant=assistant,
                phase="RESPONSE",
                decision=response_decision,
                latency_ms=latency_ms,
                policy_version=spec.policy_version,
            )

        UsageRecord.objects.create(
            organization=tenant.organization,
            assistant=assistant,
            api_key=tenant.api_key,
            kind=UsageRecord.Kind.POLICY_EVALUATION,
            outcome=(
                UsageRecord.Outcome.SUCCESS
                if decision.decision == Decision.ALLOW
                else UsageRecord.Outcome.REFUSED
            ),
            latency_ms=latency_ms,
        )

        payload = decision.to_api(policy_version=spec.policy_version)
        payload["metadata"] = _public_metadata(decision.metadata)
        payload["latency_ms"] = latency_ms
        if response_decision is not None:
            payload["response_evaluation"] = {
                **response_decision.to_api(policy_version=spec.policy_version),
                "metadata": _public_metadata(response_decision.metadata),
            }
        return Response(payload)


@extend_schema(tags=["Policies"], request=PolicyEvaluationRequestSerializer, responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)}, summary="Evaluate a response")
class PolicyEvaluateResponseView(PolicyEvaluationBaseView):
    def post(self, request):
        tenant = get_tenant(request)
        enforce_rate_limit("policy.evaluate", f"org:{tenant.organization.id}")
        serializer = PolicyEvaluationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not data.get("proposed_response"):
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"proposed_response": "This field is required."})

        spec, assistant = self._resolve_spec(tenant, data)
        engine = get_policy_engine()

        started = time.monotonic()
        with reserve_quota(tenant.organization, METRIC_POLICY):
            decision = engine.evaluate_response(
                spec=spec,
                text=data["proposed_response"],
                context_text=data.get("knowledge_context", ""),
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        record_evaluation(
            organization=tenant.organization,
            assistant=assistant,
            phase="RESPONSE",
            decision=decision,
            latency_ms=latency_ms,
            policy_version=spec.policy_version,
        )
        UsageRecord.objects.create(
            organization=tenant.organization,
            assistant=assistant,
            api_key=tenant.api_key,
            kind=UsageRecord.Kind.POLICY_EVALUATION,
            outcome=(
                UsageRecord.Outcome.SUCCESS
                if decision.decision == Decision.ALLOW
                else UsageRecord.Outcome.REFUSED
            ),
            latency_ms=latency_ms,
        )

        payload = decision.to_api(policy_version=spec.policy_version)
        payload["metadata"] = _public_metadata(decision.metadata)
        payload["latency_ms"] = latency_ms
        return Response(payload)

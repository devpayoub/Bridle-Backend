"""Usage reporting endpoints for authorized customers."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.services import get_entitlements
from apps.common.exceptions import APIError
from apps.organizations.context import CAP_USAGE_READ, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability
from apps.usage.models import UsageRecord
from apps.usage.quota import METRIC_CHAT, METRIC_POLICY, get_quota_state


def _resolve_range(request, organization) -> tuple:
    entitlements = get_entitlements(organization)
    default_start = entitlements.period_start
    default_end = timezone.now()

    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    start = parse_datetime(raw_from) if raw_from else default_start
    end = parse_datetime(raw_to) if raw_to else default_end

    if start is None or end is None:
        raise APIError("Invalid date range. Use ISO 8601 datetimes.", code="invalid_date_range")
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)
    if start >= end:
        raise APIError("'from' must be earlier than 'to'.", code="invalid_date_range")
    if end - start > timedelta(days=366):
        raise APIError("The requested range cannot exceed 366 days.", code="range_too_large")
    return start, end


class UsageBaseView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, require_capability(CAP_USAGE_READ)]

    def _queryset(self, request):
        tenant = get_tenant(request)
        start, end = _resolve_range(request, tenant.organization)
        queryset = UsageRecord.objects.filter(
            organization=tenant.organization, created_at__gte=start, created_at__lt=end
        )
        return tenant, queryset, start, end

    @staticmethod
    def _totals(queryset) -> dict:
        aggregates = queryset.aggregate(
            requests=Count("id"),
            successful=Count("id", filter=Q(outcome=UsageRecord.Outcome.SUCCESS)),
            refused=Count("id", filter=Q(outcome=UsageRecord.Outcome.REFUSED)),
            clarifications=Count("id", filter=Q(outcome=UsageRecord.Outcome.CLARIFICATION)),
            escalations=Count("id", filter=Q(outcome=UsageRecord.Outcome.ESCALATED)),
            errors=Count("id", filter=Q(outcome=UsageRecord.Outcome.ERROR)),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            average_latency_ms=Avg("latency_ms"),
        )
        return {
            "requests": aggregates["requests"] or 0,
            "successful": aggregates["successful"] or 0,
            "refused": aggregates["refused"] or 0,
            "clarifications": aggregates["clarifications"] or 0,
            "escalations": aggregates["escalations"] or 0,
            "errors": aggregates["errors"] or 0,
            "input_tokens": aggregates["input_tokens"] or 0,
            "output_tokens": aggregates["output_tokens"] or 0,
            "average_latency_ms": round(aggregates["average_latency_ms"] or 0, 2),
        }


@extend_schema(tags=["Usage"], summary="Usage summary for the current billing period", responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)})
class UsageSummaryView(UsageBaseView):
    def get(self, request):
        tenant, queryset, start, end = self._queryset(request)
        by_kind = {
            row["kind"]: row["count"]
            for row in queryset.values("kind").annotate(count=Count("id"))
        }
        chat_quota = get_quota_state(tenant.organization, METRIC_CHAT)
        policy_quota = get_quota_state(tenant.organization, METRIC_POLICY)

        return Response(
            {
                "from": start,
                "to": end,
                "totals": self._totals(queryset),
                "by_kind": by_kind,
                "quota": {
                    "chat_requests": {
                        "limit": chat_quota.limit,
                        "used": chat_quota.used,
                        "remaining": chat_quota.remaining,
                        "period_end": chat_quota.period_end,
                    },
                    "policy_evaluations": {
                        "limit": policy_quota.limit,
                        "used": policy_quota.used,
                        "remaining": policy_quota.remaining,
                        "period_end": policy_quota.period_end,
                    },
                },
                "note": "Token counts are estimates unless the provider returned usage metadata.",
            }
        )


@extend_schema(tags=["Usage"], summary="Usage grouped by assistant", responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)})
class UsageByAssistantView(UsageBaseView):
    def get(self, request):
        _, queryset, start, end = self._queryset(request)
        rows = (
            queryset.filter(assistant__isnull=False)
            .values("assistant_id", "assistant__name")
            .annotate(
                requests=Count("id"),
                successful=Count("id", filter=Q(outcome=UsageRecord.Outcome.SUCCESS)),
                refused=Count("id", filter=Q(outcome=UsageRecord.Outcome.REFUSED)),
                input_tokens=Sum("input_tokens"),
                output_tokens=Sum("output_tokens"),
                average_latency_ms=Avg("latency_ms"),
            )
            .order_by("-requests")
        )
        return Response(
            {
                "from": start,
                "to": end,
                "results": [
                    {
                        "assistant_id": str(row["assistant_id"]),
                        "assistant_name": row["assistant__name"],
                        "requests": row["requests"],
                        "successful": row["successful"],
                        "refused": row["refused"],
                        "input_tokens": row["input_tokens"] or 0,
                        "output_tokens": row["output_tokens"] or 0,
                        "average_latency_ms": round(row["average_latency_ms"] or 0, 2),
                    }
                    for row in rows
                ],
            }
        )


@extend_schema(tags=["Usage"], summary="Usage grouped by API key", responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)})
class UsageByApiKeyView(UsageBaseView):
    def get(self, request):
        _, queryset, start, end = self._queryset(request)
        rows = (
            queryset.filter(api_key__isnull=False)
            .values("api_key_id", "api_key__name", "api_key__prefix", "api_key__environment")
            .annotate(
                requests=Count("id"),
                refused=Count("id", filter=Q(outcome=UsageRecord.Outcome.REFUSED)),
                errors=Count("id", filter=Q(outcome=UsageRecord.Outcome.ERROR)),
            )
            .order_by("-requests")
        )
        return Response(
            {
                "from": start,
                "to": end,
                "results": [
                    {
                        "api_key_id": str(row["api_key_id"]),
                        "name": row["api_key__name"],
                        "prefix": row["api_key__prefix"],
                        "environment": row["api_key__environment"],
                        "requests": row["requests"],
                        "refused": row["refused"],
                        "errors": row["errors"],
                    }
                    for row in rows
                ],
            }
        )

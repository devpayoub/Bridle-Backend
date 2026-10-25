"""Cross-tenant endpoints for the platform administrator console.

Every view here is superuser-only and deliberately bypasses the tenant scoping used
by the customer API. Reads are paginated and filterable; writes are limited to the
two actions that actually need platform authority: suspending a workspace and
deactivating (or promoting) an account.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.assistants.models import Assistant
from apps.audit.models import AuditLog
from apps.audit.services import record_event
from apps.common.exceptions import Conflict
from apps.organizations.models import Organization, OrganizationMembership
from apps.platform_admin.permissions import IsPlatformAdmin
from apps.platform_admin.serializers import (
    PlatformAssistantSerializer,
    PlatformAuditLogSerializer,
    PlatformMemberSerializer,
    PlatformOrganizationSerializer,
    PlatformOrganizationUpdateSerializer,
    PlatformUserSerializer,
    PlatformUserUpdateSerializer,
)
from apps.usage.models import UsageRecord


def _owner_email_subquery():
    return Subquery(
        OrganizationMembership.objects.filter(
            organization=OuterRef("pk"), role=OrganizationMembership.Role.OWNER
        )
        .order_by("created_at")
        .values("user__email")[:1]
    )


@extend_schema(tags=["Platform admin"], summary="Platform totals and recent activity")
class PlatformOverviewView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        since = timezone.now() - timedelta(days=30)

        organizations = Organization.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=Organization.Status.ACTIVE)),
            suspended=Count("id", filter=Q(status=Organization.Status.SUSPENDED)),
            archived=Count("id", filter=Q(status=Organization.Status.ARCHIVED)),
        )

        users = User.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(is_active=True)),
            staff=Count("id", filter=Q(is_superuser=True)),
            new_30d=Count("id", filter=Q(date_joined__gte=since)),
        )

        assistants = Assistant.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=Assistant.Status.ACTIVE)),
            draft=Count("id", filter=Q(status=Assistant.Status.DRAFT)),
            paused=Count("id", filter=Q(status=Assistant.Status.PAUSED)),
            archived=Count("id", filter=Q(status=Assistant.Status.ARCHIVED)),
        )

        usage = UsageRecord.objects.filter(created_at__gte=since).aggregate(
            requests=Count("id"),
            success=Count("id", filter=Q(outcome=UsageRecord.Outcome.SUCCESS)),
            refused=Count("id", filter=Q(outcome=UsageRecord.Outcome.REFUSED)),
            errors=Count("id", filter=Q(outcome=UsageRecord.Outcome.ERROR)),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
        )

        top = (
            UsageRecord.objects.filter(created_at__gte=since)
            .values("organization_id", "organization__name")
            .annotate(requests=Count("id"))
            .order_by("-requests")[:5]
        )

        recent = (
            AuditLog.objects.select_related("actor_user", "organization").order_by("-created_at")[:6]
        )

        return Response(
            {
                "organizations": organizations,
                "users": users,
                "assistants": assistants,
                "usage_30d": {
                    **usage,
                    "tokens": (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0),
                },
                "top_organizations": [
                    {"id": row["organization_id"], "name": row["organization__name"], "requests": row["requests"]}
                    for row in top
                ],
                "recent_activity": PlatformAuditLogSerializer(recent, many=True).data,
                "generated_at": timezone.now(),
            }
        )


@extend_schema(tags=["Platform admin"], summary="Every workspace")
class PlatformOrganizationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    serializer_class = PlatformOrganizationSerializer
    search_fields = ["name", "slug"]
    filterset_fields = ["status"]
    ordering_fields = ["created_at", "name"]
    ordering = "-created_at"

    def get_queryset(self):
        return (
            Organization.objects.select_related("subscription__plan")
            .annotate(
                member_count=Count("memberships", distinct=True),
                assistant_count=Count("assistants", distinct=True),
                owner_email=_owner_email_subquery(),
            )
            .order_by("-created_at")
        )

    @extend_schema(
        tags=["Platform admin"],
        request=PlatformOrganizationUpdateSerializer,
        summary="Suspend, restore or archive a workspace",
    )
    @action(detail=True, methods=["patch"], url_path="status")
    def set_status(self, request, pk=None):
        organization = self.get_object()
        serializer = PlatformOrganizationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.validated_data["status"]

        previous = organization.status
        if target == previous:
            return Response(self.get_serializer(organization).data)

        organization.status = target
        organization.save(update_fields=["status", "updated_at"])
        record_event(
            action=AuditLog.Action.PLATFORM_ORGANIZATION_STATUS,
            organization=organization,
            actor_user=request.user,
            target_type="organization",
            target_id=organization.id,
            metadata={"status": target, "previous_status": previous},
            request=request,
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=organization.pk)).data)

    @extend_schema(tags=["Platform admin"], summary="Members of a workspace")
    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        organization = self.get_object()
        rows = organization.memberships.select_related("user").order_by("created_at")
        return Response({"results": PlatformMemberSerializer(rows, many=True).data})


@extend_schema(tags=["Platform admin"], summary="Every account")
class PlatformUserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    serializer_class = PlatformUserSerializer
    search_fields = ["email", "full_name"]
    filterset_fields = ["is_active", "is_superuser"]
    ordering_fields = ["date_joined", "last_login", "email"]
    ordering = "-date_joined"

    def get_queryset(self):
        return User.objects.annotate(
            organization_count=Count("organization_memberships", distinct=True)
        ).order_by("-date_joined")

    @extend_schema(
        tags=["Platform admin"],
        request=PlatformUserUpdateSerializer,
        summary="Activate, deactivate or promote an account",
    )
    @action(detail=True, methods=["patch"], url_path="status")
    def set_status(self, request, pk=None):
        user = self.get_object()
        serializer = PlatformUserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if user.pk == request.user.pk and (
            data.get("is_active") is False or data.get("is_superuser") is False
        ):
            raise Conflict(
                "You cannot deactivate or demote your own account.", code="self_lockout"
            )

        changed = {}
        for field in ("is_active", "is_superuser"):
            if field in data and getattr(user, field) != data[field]:
                changed[field] = data[field]
                setattr(user, field, data[field])

        if not changed:
            return Response(self.get_serializer(user).data)

        if "is_superuser" in changed:
            # A superuser without staff cannot reach Django admin; keep them in sync.
            user.is_staff = changed["is_superuser"]
        user.save(update_fields=[*changed.keys(), "is_staff", "updated_at"])

        record_event(
            action=AuditLog.Action.PLATFORM_USER_UPDATED,
            actor_user=request.user,
            target_type="user",
            target_id=user.id,
            metadata={"email": user.email, "changes": {k: str(v) for k, v in changed.items()}},
            request=request,
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=user.pk)).data)


@extend_schema(tags=["Platform admin"], summary="Every assistant")
class PlatformAssistantViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    serializer_class = PlatformAssistantSerializer
    search_fields = ["name", "organization__name"]
    filterset_fields = ["status", "domain_type", "organization"]
    ordering = "-created_at"

    def get_queryset(self):
        return Assistant.objects.select_related("organization").order_by("-created_at")


@extend_schema(tags=["Platform admin"], summary="Audit trail across all workspaces")
class PlatformAuditLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    serializer_class = PlatformAuditLogSerializer
    filterset_fields = ["action", "target_type", "organization"]
    search_fields = ["actor_user__email", "organization__name"]
    ordering = "-created_at"

    def get_queryset(self):
        return AuditLog.objects.select_related("actor_user", "organization").order_by("-created_at")

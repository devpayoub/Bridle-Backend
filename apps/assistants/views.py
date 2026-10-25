"""Assistant management API."""
from __future__ import annotations

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.assistants import services
from apps.assistants.models import Assistant
from apps.assistants.serializers import (
    AssistantDraftRequestSerializer,
    AssistantEvaluationSerializer,
    AssistantSerializer,
    AssistantUpdateSerializer,
    AssistantUsageQuerySerializer,
    AssistantWriteSerializer,
)
from apps.common.exceptions import TenantAccessDenied
from apps.organizations.context import (
    CAP_ASSISTANT_READ,
    CAP_ASSISTANT_WRITE,
    CAP_POLICY_EVALUATE,
    CAP_POLICY_READ,
    CAP_POLICY_WRITE,
    CAP_USAGE_READ,
    get_tenant,
)
from apps.organizations.permissions import IsTenantMember, require_capability
from apps.policies.serializers import PolicyUpdateSerializer
from apps.policies.services import run_evaluation_set, serialize_policy, update_policy


@extend_schema_view(
    list=extend_schema(tags=["Assistants"], summary="List assistants"),
    create=extend_schema(tags=["Assistants"], summary="Create an assistant"),
    retrieve=extend_schema(tags=["Assistants"], summary="Retrieve an assistant"),
    partial_update=extend_schema(tags=["Assistants"], summary="Update assistant configuration"),
)
class AssistantViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "patch", "put", "head", "options"]
    filterset_fields = ["status", "domain_type"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at", "name"]
    ordering = "-created_at"

    def get_permissions(self):
        action_capabilities = {
            "list": CAP_ASSISTANT_READ,
            "retrieve": CAP_ASSISTANT_READ,
            "create": CAP_ASSISTANT_WRITE,
            "draft": CAP_ASSISTANT_WRITE,
            "partial_update": CAP_ASSISTANT_WRITE,
            "update": CAP_ASSISTANT_WRITE,
            "activate": CAP_ASSISTANT_WRITE,
            "pause": CAP_ASSISTANT_WRITE,
            "archive": CAP_ASSISTANT_WRITE,
            "duplicate": CAP_ASSISTANT_WRITE,
            "usage": CAP_USAGE_READ,
            "evaluations": CAP_POLICY_EVALUATE,
            "policy": CAP_POLICY_READ,
            "update_policy": CAP_POLICY_WRITE,
        }
        capability = action_capabilities.get(self.action, CAP_ASSISTANT_READ)
        return [IsAuthenticated(), IsTenantMember(), require_capability(capability)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            # Schema generation must not resolve a tenant.
            return Assistant.objects.none()
        tenant = get_tenant(self.request)
        return (
            Assistant.objects.filter(organization=tenant.organization)
            .select_related("policy", "organization")
            .prefetch_related("knowledge_bases")
        )

    def get_serializer_class(self):
        if self.action == "create":
            return AssistantWriteSerializer
        if self.action in {"update", "partial_update"}:
            return AssistantUpdateSerializer
        if self.action == "evaluations":
            return AssistantEvaluationSerializer
        if self.action == "policy":
            return PolicyUpdateSerializer
        return AssistantSerializer

    def create(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        serializer = AssistantWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assistant = services.create_assistant(
            organization=tenant.organization, data=serializer.validated_data, actor=tenant.actor_user
        )
        assistant = self.get_queryset().get(pk=assistant.pk)
        return Response(AssistantSerializer(assistant).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Assistants"],
        request=AssistantDraftRequestSerializer,
        summary="Draft an assistant configuration from a domain description",
    )
    @action(detail=False, methods=["post"], url_path="draft")
    def draft(self, request):
        """Suggest a starting configuration for the new-assistant form.

        Uses the workspace's AI provider when one is configured and falls back to a
        deterministic template otherwise. Nothing is persisted.
        """
        tenant = get_tenant(request)
        serializer = AssistantDraftRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = services.draft_assistant(
            organization=tenant.organization,
            description=serializer.validated_data["description"],
        )
        return Response(payload)

    def update(self, request, *args, **kwargs):
        """PUT shares the PATCH path.

        ``AssistantUpdateSerializer`` is a plain ``Serializer`` — writes go through
        ``services.update_assistant`` — so DRF's default ``UpdateModelMixin`` would
        call ``save()`` on it and fail with a 500. Both verbs are routed to the same
        handler instead.
        """
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        assistant = self.get_object()
        serializer = AssistantUpdateSerializer(data=request.data, partial=True, instance=assistant)
        serializer.is_valid(raise_exception=True)
        assistant = services.update_assistant(
            assistant, data=serializer.validated_data, actor=tenant.actor_user
        )
        assistant = self.get_queryset().get(pk=assistant.pk)
        return Response(AssistantSerializer(assistant).data)

    # -- lifecycle -------------------------------------------------------
    def _transition(self, request, target: str):
        tenant = get_tenant(request)
        assistant = self.get_object()
        assistant = services.change_status(assistant, target, actor=tenant.actor_user)
        assistant = self.get_queryset().get(pk=assistant.pk)
        return Response(AssistantSerializer(assistant).data)

    @extend_schema(tags=["Assistants"], summary="Activate an assistant", request=None)
    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        return self._transition(request, Assistant.Status.ACTIVE)

    @extend_schema(tags=["Assistants"], summary="Pause an assistant", request=None)
    @action(detail=True, methods=["post"], url_path="pause")
    def pause(self, request, pk=None):
        return self._transition(request, Assistant.Status.PAUSED)

    @extend_schema(tags=["Assistants"], summary="Archive an assistant", request=None)
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        return self._transition(request, Assistant.Status.ARCHIVED)

    @extend_schema(tags=["Assistants"], summary="Duplicate an assistant", request=None)
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        tenant = get_tenant(request)
        assistant = self.get_object()
        duplicate = services.duplicate_assistant(
            assistant, actor=tenant.actor_user, name=request.data.get("name")
        )
        duplicate = self.get_queryset().get(pk=duplicate.pk)
        return Response(AssistantSerializer(duplicate).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["Assistants"], summary="Assistant usage statistics")
    @action(detail=True, methods=["get"], url_path="usage")
    def usage(self, request, pk=None):
        assistant = self.get_object()
        query = AssistantUsageQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        stats = services.usage_statistics(
            assistant,
            since=query.validated_data.get("since"),
            until=query.validated_data.get("until"),
        )
        return Response(stats)

    @extend_schema(tags=["Policies"], summary="Run policy evaluation tests")
    @action(detail=True, methods=["post"], url_path="evaluations")
    def evaluations(self, request, pk=None):
        assistant = self.get_object()
        serializer = AssistantEvaluationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = run_evaluation_set(assistant=assistant, cases=serializer.validated_data["cases"])
        return Response(report)

    # -- policy ----------------------------------------------------------
    @extend_schema(tags=["Policies"], summary="Get assistant policy")
    @action(detail=True, methods=["get", "put"], url_path="policy")
    def policy(self, request, pk=None):
        assistant = self.get_object()
        if request.method == "PUT":
            tenant = get_tenant(request)
            if not tenant.can(CAP_POLICY_WRITE):
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied("Your role cannot modify policies.")
            serializer = PolicyUpdateSerializer(data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            update_policy(assistant=assistant, data=serializer.validated_data, actor=tenant.actor_user)
            assistant.refresh_from_db()
        return Response(serialize_policy(assistant))

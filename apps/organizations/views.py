"""Organization and membership API."""
from __future__ import annotations

from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.exceptions import TenantAccessDenied
from apps.organizations import services
from apps.organizations.context import (
    CAP_MEMBER_READ,
    CAP_MEMBER_WRITE,
    CAP_ORG_READ,
    CAP_ORG_WRITE,
    resolve_tenant,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.organizations.serializers import (
    AISettingsUpdateSerializer,
    MemberCreateSerializer,
    MemberRoleUpdateSerializer,
    MembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
)


def _require_user(request):
    if getattr(request.user, "pk", None) is None:
        raise PermissionDenied("This endpoint requires a user account, not an API key.")


@extend_schema_view(
    list=extend_schema(tags=["Organizations"], summary="List organizations you belong to"),
    create=extend_schema(tags=["Organizations"], summary="Create an organization"),
    retrieve=extend_schema(tags=["Organizations"], summary="Retrieve an organization"),
    partial_update=extend_schema(tags=["Organizations"], summary="Update an organization"),
)
class OrganizationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    # `put` is required by the ai-settings action; the organisation itself is still
    # patched (PUT on the detail route is not mapped).
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        user = self.request.user
        if getattr(user, "pk", None) is None:
            return Organization.objects.none()
        return (
            Organization.objects.filter(memberships__user=user)
            .select_related("subscription__plan")
            .annotate(member_count=Count("memberships", distinct=True))
            .order_by("name")
            .distinct()
        )

    def get_serializer_class(self):
        if self.action == "create":
            return OrganizationCreateSerializer
        if self.action in {"update", "partial_update"}:
            return OrganizationUpdateSerializer
        return OrganizationSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        role_by_org = {
            str(m.organization_id): m.role
            for m in OrganizationMembership.objects.filter(user=request.user)
        }
        rows = page if page is not None else queryset
        serializer = OrganizationSerializer(rows, many=True, context={"roles": role_by_org})
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        _require_user(request)
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = services.create_organization(
            owner=request.user,
            name=serializer.validated_data["name"],
            timezone=serializer.validated_data.get("timezone", "UTC"),
        )
        organization = self.get_queryset().get(pk=organization.pk)
        return Response(
            OrganizationSerializer(
                organization,
                context={"roles": {str(organization.id): OrganizationMembership.Role.OWNER}},
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, *args, **kwargs):
        tenant = resolve_tenant(request, organization_id=kwargs["pk"])
        request.tenant = tenant
        organization = self.get_queryset().get(pk=tenant.organization.pk)
        return Response(
            OrganizationSerializer(
                organization, context={"roles": {str(organization.id): tenant.role}}
            ).data
        )

    def partial_update(self, request, *args, **kwargs):
        tenant = resolve_tenant(request, organization_id=kwargs["pk"])
        request.tenant = tenant
        if not tenant.can(CAP_ORG_WRITE):
            raise PermissionDenied("Your role does not allow changing organization settings.")
        serializer = OrganizationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = services.update_organization(
            tenant.organization, data=serializer.validated_data, actor=tenant.actor_user
        )
        return Response(OrganizationSerializer(organization).data)

    # -- members ---------------------------------------------------------
    @extend_schema(tags=["Organizations"], summary="List members", methods=["GET"])
    @extend_schema(
        tags=["Organizations"],
        request=MemberCreateSerializer,
        summary="Add a member",
        responses={201: MembershipSerializer},
        methods=["POST"],
    )
    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        """List members (GET) or add one (POST).

        Both verbs share a single action on purpose: DRF registers extra actions
        by URL path, so two methods declaring ``url_path="members"`` would shadow
        each other and one of the verbs would answer 405.
        """
        tenant = resolve_tenant(request, organization_id=pk)

        if request.method == "POST":
            if not tenant.can(CAP_MEMBER_WRITE):
                raise PermissionDenied("Your role cannot manage members.")
            serializer = MemberCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            membership = services.add_member(
                organization=tenant.organization,
                email=serializer.validated_data["email"],
                role=serializer.validated_data["role"],
                actor=tenant.actor_user,
            )
            return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)

        if not tenant.can(CAP_MEMBER_READ):
            raise PermissionDenied("Your role cannot view members.")
        queryset = OrganizationMembership.objects.select_related("user").filter(
            organization=tenant.organization
        )
        page = self.paginate_queryset(queryset)
        serializer = MembershipSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    # -- AI provider settings ---------------------------------------------
    @extend_schema(tags=["Organizations"], summary="Workspace AI provider settings", methods=["GET"])
    @extend_schema(
        tags=["Organizations"],
        request=AISettingsUpdateSerializer,
        summary="Update workspace AI provider settings",
        methods=["PUT"],
    )
    @action(detail=True, methods=["get", "put", "delete"], url_path="ai-settings")
    def ai_settings(self, request, pk=None):
        """Read, update or clear the workspace's AI credentials.

        The API key is write-only: reads return ``has_key`` and ``key_last4`` so an
        operator can identify the installed key without being able to copy it back.
        """
        tenant = resolve_tenant(request, organization_id=pk)

        if request.method == "GET":
            if not tenant.can(CAP_ORG_READ):
                raise PermissionDenied("Your role cannot view this workspace.")
            return Response(services.describe_ai_settings(tenant.organization))

        if not tenant.can(CAP_ORG_WRITE):
            raise PermissionDenied("Your role cannot change AI settings.")

        if request.method == "DELETE":
            services.clear_ai_settings(
                organization=tenant.organization, actor=tenant.actor_user, request=request
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = AISettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = services.update_ai_settings(
            organization=tenant.organization,
            data=serializer.validated_data,
            actor=tenant.actor_user,
            request=request,
        )
        return Response(payload)

    @extend_schema(tags=["Organizations"], request=None, summary="Test the workspace AI provider")
    @action(detail=True, methods=["post"], url_path="ai-settings/test")
    def test_ai_settings(self, request, pk=None):
        tenant = resolve_tenant(request, organization_id=pk)
        if not tenant.can(CAP_ORG_WRITE):
            raise PermissionDenied("Your role cannot test AI settings.")
        return Response(services.test_ai_settings(tenant.organization))

    @extend_schema(
        tags=["Organizations"],
        summary="Change a member role or remove a member",
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="User id of the membership to update.",
            )
        ],
    )
    @action(detail=True, methods=["patch", "delete"], url_path=r"members/(?P<user_id>[^/.]+)")
    def member_detail(self, request, pk=None, user_id=None):
        tenant = resolve_tenant(request, organization_id=pk)
        if not tenant.can(CAP_MEMBER_WRITE):
            raise PermissionDenied("Your role cannot manage members.")

        membership = (
            OrganizationMembership.objects.select_related("user")
            .filter(organization=tenant.organization, user_id=user_id)
            .first()
        )
        if membership is None:
            raise TenantAccessDenied("Member not found.")

        is_owner_actor = tenant.role == OrganizationMembership.Role.OWNER
        if membership.role == OrganizationMembership.Role.OWNER and not is_owner_actor:
            raise PermissionDenied("Only an owner can change or remove an owner.")

        if request.method == "DELETE":
            services.remove_member(membership=membership, actor=tenant.actor_user)
            return Response(status=status.HTTP_204_NO_CONTENT)

        requested_role = request.data.get("role")
        if requested_role == OrganizationMembership.Role.OWNER and not is_owner_actor:
            raise PermissionDenied("Only an owner can grant ownership.")

        serializer = MemberRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.update_member_role(
            membership=membership, role=serializer.validated_data["role"], actor=tenant.actor_user
        )
        return Response(MembershipSerializer(membership).data)

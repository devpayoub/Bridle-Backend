"""API key management endpoints."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api_keys import services
from apps.api_keys.models import APIKey
from apps.api_keys.serializers import APIKeyCreateSerializer, APIKeyCreatedSerializer, APIKeySerializer
from apps.assistants.models import Assistant
from apps.billing.services import assert_can_create_api_key
from apps.common.exceptions import APIError, Conflict, TenantAccessDenied
from apps.organizations.context import CAP_APIKEY_READ, CAP_APIKEY_WRITE, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability


@extend_schema_view(
    list=extend_schema(tags=["API keys"], summary="List API keys"),
    create=extend_schema(tags=["API keys"], summary="Create an API key"),
)
class APIKeyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "delete", "head", "options"]
    ordering = "-created_at"

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), IsTenantMember(), require_capability(CAP_APIKEY_READ)()]
        return [IsAuthenticated(), IsTenantMember(), require_capability(CAP_APIKEY_WRITE)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            # Schema generation must not resolve a tenant.
            return APIKey.objects.none()
        tenant = get_tenant(self.request)
        return APIKey.objects.filter(organization=tenant.organization).select_related("assistant")

    def get_serializer_class(self):
        if self.action == "create":
            return APIKeyCreateSerializer
        return APIKeySerializer

    def create(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        active_count = self.get_queryset().filter(revoked_at__isnull=True).count()
        assert_can_create_api_key(tenant.organization, current_count=active_count)

        assistant = None
        if data.get("assistant_id"):
            assistant = Assistant.objects.filter(
                id=data["assistant_id"], organization=tenant.organization
            ).first()
            if assistant is None:
                raise TenantAccessDenied("Assistant not found.")

        expires_at = None
        if data.get("expires_in_days"):
            expires_at = timezone.now() + timedelta(days=data["expires_in_days"])

        record, plaintext = services.create_api_key(
            organization=tenant.organization,
            name=data["name"],
            environment=data["environment"],
            scopes=data.get("scopes"),
            assistant=assistant,
            created_by=tenant.actor_user,
            expires_at=expires_at,
        )

        from apps.audit.models import AuditLog
        from apps.audit.services import record_event

        record_event(
            action=AuditLog.Action.API_KEY_CREATED,
            organization=tenant.organization,
            actor_user=tenant.actor_user,
            api_key=record,
            target_type="api_key",
            target_id=record.id,
            metadata={"environment": record.environment, "name": record.name},
            request=request,
        )

        payload = APIKeyCreatedSerializer(record).data
        payload["key"] = plaintext
        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["API keys"], summary="Rotate an API key")
    @action(detail=True, methods=["post"], url_path="rotate")
    def rotate(self, request, pk=None):
        tenant = get_tenant(request)
        api_key = self.get_object()
        new_key, plaintext = services.rotate_api_key(api_key, actor=tenant.actor_user)

        from apps.audit.models import AuditLog
        from apps.audit.services import record_event

        record_event(
            action=AuditLog.Action.API_KEY_ROTATED,
            organization=tenant.organization,
            actor_user=tenant.actor_user,
            target_type="api_key",
            target_id=new_key.id,
            metadata={"api_key_id": str(api_key.id)},
            request=request,
        )
        payload = APIKeyCreatedSerializer(new_key).data
        payload["key"] = plaintext
        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["API keys"], summary="Revoke an API key")
    @action(detail=True, methods=["post", "delete"], url_path="revoke")
    def revoke(self, request, pk=None):
        tenant = get_tenant(request)
        api_key = self.get_object()
        if api_key.is_revoked:
            return Response(status=status.HTTP_204_NO_CONTENT)
        services.revoke_api_key(api_key)

        from apps.audit.models import AuditLog
        from apps.audit.services import record_event

        record_event(
            action=AuditLog.Action.API_KEY_REVOKED,
            organization=tenant.organization,
            actor_user=tenant.actor_user,
            target_type="api_key",
            target_id=api_key.id,
            metadata={"name": api_key.name},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def destroy(self, request, *args, **kwargs):
        """``DELETE /api-keys/{id}/`` removes a revoked key.

        An active key must be revoked first: revocation writes an audit row and makes
        the failure visible to whoever is still using it. Deleting a live key outright
        would erase the trail of why it stopped working.
        """
        from apps.audit.models import AuditLog
        from apps.audit.services import record_event

        tenant = get_tenant(request)
        api_key = self.get_object()
        if not api_key.is_revoked:
            raise Conflict("Revoke the key before deleting it.", code="key_active")

        key_id = api_key.id
        name = api_key.name
        api_key.delete()
        record_event(
            action=AuditLog.Action.API_KEY_DELETED,
            organization=tenant.organization,
            actor_user=tenant.actor_user,
            target_type="api_key",
            target_id=key_id,
            metadata={"name": name},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

"""Channel management endpoints (console-facing).

Every action is capability-gated: read for listing, write for anything that
changes where a client's customers get sent.
"""
from __future__ import annotations

from django.conf import settings
from django.http import Http404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.channels import services
from apps.channels.models import Channel
from apps.channels.serializers import (
    ChannelConnectSerializer,
    ChannelSerializer,
    ChannelTestResultSerializer,
    ChannelUpdateSerializer,
)
from apps.organizations.context import CAP_CHANNEL_READ, CAP_CHANNEL_WRITE, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability


@extend_schema_view(
    list=extend_schema(tags=["Channels"], summary="List connected channels"),
    retrieve=extend_schema(tags=["Channels"], summary="Retrieve a channel"),
    create=extend_schema(tags=["Channels"], summary="Connect a Telegram bot"),
    destroy=extend_schema(tags=["Channels"], summary="Disconnect a channel"),
)
class ChannelViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    ordering = "-created_at"

    def initial(self, request, *args, **kwargs):
        # Feature flag: the whole surface disappears when channels are off.
        if not settings.CHANNELS_ENABLED:
            raise Http404
        super().initial(request, *args, **kwargs)

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), IsTenantMember(), require_capability(CAP_CHANNEL_READ)()]
        return [IsAuthenticated(), IsTenantMember(), require_capability(CAP_CHANNEL_WRITE)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Channel.objects.none()
        tenant = get_tenant(self.request)
        return Channel.objects.filter(organization=tenant.organization).select_related("assistant")

    def get_serializer_class(self):
        if self.action == "create":
            return ChannelConnectSerializer
        if self.action == "partial_update":
            return ChannelUpdateSerializer
        if self.action == "test":
            return ChannelTestResultSerializer
        return ChannelSerializer

    def _can_write(self) -> bool:
        if getattr(self, "swagger_fake_view", False):
            return False
        try:
            return get_tenant(self.request).can(CAP_CHANNEL_WRITE)
        except Exception:  # noqa: BLE001 - schema generation has no tenant
            return False

    def get_serializer(self, *args, **kwargs):
        kwargs.setdefault(
            "context", {**self.get_serializer_context(), "include_webhook_url": self._can_write()}
        )
        return super().get_serializer(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        serializer = ChannelConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        channel = services.connect_channel(
            tenant=tenant,
            assistant_id=data["assistant_id"],
            bot_token=data["bot_token"],
            name=data.get("name", ""),
            request=request,
        )
        return Response(self.get_serializer(channel).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        channel = self.get_object()
        serializer = ChannelUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = services.update_channel(
            channel=channel, data=serializer.validated_data, tenant=tenant, request=request
        )
        return Response(self.get_serializer(channel).data)

    def destroy(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        channel = self.get_object()
        services.disconnect_channel(channel=channel, tenant=tenant, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["Channels"], summary="Probe the bot token")
    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        channel = self.get_object()
        return Response(services.test_channel(channel))

    @extend_schema(tags=["Channels"], summary="Rotate the webhook key and secret")
    @action(detail=True, methods=["post"], url_path="rotate-webhook")
    def rotate_webhook(self, request, pk=None):
        tenant = get_tenant(request)
        channel = services.rotate_webhook(channel=self.get_object(), tenant=tenant, request=request)
        return Response(self.get_serializer(channel).data)

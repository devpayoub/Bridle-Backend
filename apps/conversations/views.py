"""Chat completion and conversation history endpoints."""
from __future__ import annotations

from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.chat import handle_chat_request
from apps.conversations.models import Conversation, Message
from apps.conversations.serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ConversationSerializer,
    MessageSerializer,
)
from apps.conversations.services import anonymize_conversation
from apps.organizations.context import CAP_CONVERSATION_DELETE, CAP_CONVERSATION_READ, get_tenant
from apps.organizations.permissions import CanChat, IsTenantMember, require_capability


@extend_schema(tags=["Chat"], request=ChatRequestSerializer, responses={200: ChatResponseSerializer})
class ChatCompletionsView(APIView):
    """Main chat endpoint.

    Authenticate with an API key (`Authorization: Bearer sk_...`) or a user JWT.
    The organization is always derived from the credential, never from the body.
    Send an `Idempotency-Key` header to make retries safe.
    """

    permission_classes = [IsAuthenticated, IsTenantMember, CanChat]

    def post(self, request):
        tenant = get_tenant(request)
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        outcome = handle_chat_request(
            tenant=tenant,
            assistant_id=data["assistant_id"],
            message=data["message"],
            conversation_id=data.get("conversation_id"),
            metadata=data.get("metadata") or {},
            idempotency_key=(request.headers.get("Idempotency-Key") or "")[:128],
            request_id=getattr(request, "request_id", ""),
        )
        return Response(outcome.payload, status=outcome.http_status)


@extend_schema_view(
    list=extend_schema(tags=["Conversations"], summary="List conversations"),
    retrieve=extend_schema(tags=["Conversations"], summary="Retrieve a conversation"),
)
class ConversationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "delete", "head", "options"]
    filterset_fields = ["assistant_id", "status", "external_user_id"]
    ordering_fields = ["last_message_at", "created_at"]
    ordering = "-last_message_at"

    def get_permissions(self):
        capability = CAP_CONVERSATION_READ if self.request.method in {"GET", "HEAD", "OPTIONS"} else CAP_CONVERSATION_DELETE
        return [IsAuthenticated(), IsTenantMember(), require_capability(capability)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            # Schema generation must not resolve a tenant.
            return Conversation.objects.none()
        tenant = get_tenant(self.request)
        return (
            Conversation.objects.filter(organization=tenant.organization)
            .select_related("assistant")
            .annotate(
                message_count=Count("messages", filter=Q(messages__status=Message.Status.COMPLETED))
            )
        )

    def get_serializer_class(self):
        return ConversationSerializer

    @extend_schema(tags=["Conversations"], summary="List messages in a conversation")
    @action(detail=True, methods=["get"], url_path="messages")
    def messages(self, request, pk=None):
        conversation = self.get_object()
        queryset = conversation.messages.all().order_by("created_at")
        role = request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role.upper())
        page = self.paginate_queryset(queryset)
        serializer = MessageSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(tags=["Conversations"], summary="Anonymise a conversation")
    def destroy(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        conversation = self.get_object()
        anonymize_conversation(
            conversation, actor=tenant.actor_user, reason="user_request"
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

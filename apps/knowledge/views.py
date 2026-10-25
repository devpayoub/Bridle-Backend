"""Knowledge base and document API."""
from __future__ import annotations

from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.exceptions import TenantAccessDenied
from apps.knowledge import services
from apps.knowledge.ingestion import enqueue_ingestion
from apps.knowledge.models import Document, KnowledgeBase
from apps.knowledge.serializers import (
    DocumentSerializer,
    DocumentUploadSerializer,
    KnowledgeBaseCreateSerializer,
    KnowledgeBaseSerializer,
)
from apps.organizations.context import CAP_KNOWLEDGE_READ, CAP_KNOWLEDGE_WRITE, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability
from apps.usage.rate_limit import enforce_rate_limit


class KnowledgeBaseViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "delete", "head", "options"]
    search_fields = ["name", "description"]
    ordering = "name"

    def get_permissions(self):
        capability = CAP_KNOWLEDGE_READ if self.request.method in {"GET", "HEAD", "OPTIONS"} else CAP_KNOWLEDGE_WRITE
        return [IsAuthenticated(), IsTenantMember(), require_capability(capability)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            # Schema generation must not resolve a tenant.
            return KnowledgeBase.objects.none()
        tenant = get_tenant(self.request)
        return KnowledgeBase.objects.filter(organization=tenant.organization).annotate(
            document_count=Count("documents", filter=Q(documents__deleted_at__isnull=True), distinct=True)
        )

    def get_serializer_class(self):
        return KnowledgeBaseCreateSerializer if self.action == "create" else KnowledgeBaseSerializer

    def create(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        serializer = KnowledgeBaseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        knowledge_base = services.create_knowledge_base(
            organization=tenant.organization,
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            actor=tenant.actor_user,
        )
        return Response(
            KnowledgeBaseSerializer(knowledge_base).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Knowledge"], summary="List documents in a knowledge base")
    @action(detail=True, methods=["get", "post"], url_path="documents", parser_classes=[MultiPartParser, FormParser])
    def documents(self, request, pk=None):
        tenant = get_tenant(request)
        knowledge_base = self.get_object()

        if request.method == "GET":
            queryset = knowledge_base.documents.filter(deleted_at__isnull=True).order_by("-created_at")
            page = self.paginate_queryset(queryset)
            serializer = DocumentSerializer(page if page is not None else queryset, many=True)
            if page is not None:
                return self.get_paginated_response(serializer.data)
            return Response(serializer.data)

        if not tenant.can(CAP_KNOWLEDGE_WRITE):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("Your role cannot upload documents.")
        enforce_rate_limit("knowledge.upload", f"org:{tenant.organization.id}")

        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = services.create_document(
            organization=tenant.organization,
            knowledge_base=knowledge_base,
            upload=serializer.validated_data["file"],
            title=serializer.validated_data.get("title", ""),
            actor=tenant.actor_user,
        )
        enqueue_ingestion(document)
        document.refresh_from_db()
        return Response(DocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    retrieve=extend_schema(tags=["Knowledge"], summary="Retrieve a document"),
    destroy=extend_schema(tags=["Knowledge"], summary="Delete a document"),
)
class DocumentViewSet(
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "delete", "post", "head", "options"]

    def get_permissions(self):
        capability = CAP_KNOWLEDGE_READ if self.request.method in {"GET", "HEAD", "OPTIONS"} else CAP_KNOWLEDGE_WRITE
        return [IsAuthenticated(), IsTenantMember(), require_capability(capability)()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Document.objects.none()
        tenant = get_tenant(self.request)
        return Document.objects.filter(
            organization=tenant.organization, deleted_at__isnull=True
        ).select_related("knowledge_base")

    def get_serializer_class(self):
        return DocumentSerializer

    def destroy(self, request, *args, **kwargs):
        tenant = get_tenant(request)
        document = self.get_object()
        services.delete_document(document, actor=tenant.actor_user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["Knowledge"], summary="Reprocess a document", request=None)
    @action(detail=True, methods=["post"], url_path="reprocess")
    def reprocess(self, request, pk=None):
        tenant = get_tenant(request)
        document = self.get_object()
        job = services.reprocess_document(document, actor=tenant.actor_user)
        enqueue_ingestion(document, job=job)
        document.refresh_from_db()
        return Response(
            {
                "document": DocumentSerializer(document).data,
                "job_id": str(job.id),
                "status": document.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(tags=["Knowledge"], summary="Document processing status")
    @action(detail=True, methods=["get"], url_path="status")
    def processing_status(self, request, pk=None):
        document = self.get_object()
        return Response(services.processing_status(document))

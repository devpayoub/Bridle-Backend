"""Audit log access (admin roles only)."""
from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from apps.organizations.context import CAP_AUDIT_READ, get_tenant
from apps.organizations.permissions import IsTenantMember, require_capability


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember, require_capability(CAP_AUDIT_READ)]
    http_method_names = ["get", "head", "options"]
    filterset_fields = ["action", "target_type", "target_id"]
    ordering = "-created_at"
    ordering_fields = ["created_at"]
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            # Schema generation must not resolve a tenant.
            return AuditLog.objects.none()
        tenant = get_tenant(self.request)
        return AuditLog.objects.filter(organization=tenant.organization).select_related(
            "actor_user", "api_key"
        )

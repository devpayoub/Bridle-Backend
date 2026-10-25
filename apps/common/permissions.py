"""Generic, reusable DRF permissions."""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOrganizationMember(BasePermission):
    """Any authenticated principal that resolved an organization context."""

    message = "You do not have access to this organization."

    def has_permission(self, request, view) -> bool:
        return getattr(request, "tenant", None) is not None


class ReadOnlyOrOrganizationEditor(BasePermission):
    """Safe methods for every member; writes require an editing role."""

    message = "Your role does not allow modifying this resource."

    def has_permission(self, request, view) -> bool:
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return tenant.can_edit

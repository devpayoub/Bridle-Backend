"""DRF permissions built on the capability matrix."""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.organizations.context import (
    CAP_ASSISTANT_WRITE,
    CAP_CHAT_WRITE,
    TenantContext,
    get_tenant,
)


class IsTenantMember(BasePermission):
    """Any authenticated principal that can resolve a tenant context."""

    message = "You do not have access to this organization."

    def has_permission(self, request, view) -> bool:
        get_tenant(request)
        return True


def require_capability(capability: str):
    """Permission factory: caller must hold ``capability`` in their tenant."""

    class _CapabilityPermission(BasePermission):
        message = "Your role does not permit this operation."

        def has_permission(self, request, view) -> bool:
            tenant: TenantContext = get_tenant(request)
            return tenant.can(capability)

    _CapabilityPermission.__name__ = f"Require_{capability.replace(':', '_')}"
    return _CapabilityPermission


class ReadOrWriteCapability(BasePermission):
    """Safe methods need ``read_capability``; unsafe need ``write_capability``."""

    read_capability: str = ""
    write_capability: str = CAP_ASSISTANT_WRITE

    def has_permission(self, request, view) -> bool:
        tenant = get_tenant(request)
        required = self.read_capability if request.method in SAFE_METHODS else self.write_capability
        return tenant.can(required)


class CanChat(BasePermission):
    message = "This credential cannot be used for chat completions."

    def has_permission(self, request, view) -> bool:
        return get_tenant(request).can(CAP_CHAT_WRITE)

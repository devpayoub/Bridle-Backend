"""Access control for the platform (cross-tenant) surface."""
from __future__ import annotations

from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Superusers only.

    This is deliberately *not* a tenant capability: every endpoint behind it reads
    across organisations, so it must never be reachable with a workspace role or an
    API key. ``is_superuser`` is only ever true for a Django account.
    """

    message = "Platform administrator access required."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and getattr(user, "is_superuser", False))

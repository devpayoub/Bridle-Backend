"""Role/capability matrix and tenant resolution.

The matrix is the single source of truth for authorization. Both dashboard users
(role based) and API keys (scope based) are projected onto the same capability
vocabulary, so views never branch on "is this a user or a key".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rest_framework import status

from apps.common.exceptions import APIError, TenantAccessDenied
from apps.organizations.models import Organization, OrganizationMembership

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
CAP_ORG_READ = "organization:read"
CAP_ORG_WRITE = "organization:write"
CAP_ORG_DELETE = "organization:delete"
CAP_MEMBER_READ = "member:read"
CAP_MEMBER_WRITE = "member:write"
CAP_ASSISTANT_READ = "assistant:read"
CAP_ASSISTANT_WRITE = "assistant:write"
CAP_POLICY_READ = "policy:read"
CAP_POLICY_WRITE = "policy:write"
CAP_POLICY_EVALUATE = "policy:evaluate"
CAP_KNOWLEDGE_READ = "knowledge:read"
CAP_KNOWLEDGE_WRITE = "knowledge:write"
CAP_APIKEY_READ = "apikey:read"
CAP_APIKEY_WRITE = "apikey:write"
CAP_CONVERSATION_READ = "conversation:read"
CAP_CONVERSATION_DELETE = "conversation:delete"
CAP_CHAT_WRITE = "chat:write"
CAP_USAGE_READ = "usage:read"
CAP_AUDIT_READ = "audit:read"
CAP_BILLING_READ = "billing:read"
CAP_CHANNEL_READ = "channel:read"
CAP_CHANNEL_WRITE = "channel:write"

ALL_CAPABILITIES = frozenset(
    {
        CAP_ORG_READ,
        CAP_ORG_WRITE,
        CAP_ORG_DELETE,
        CAP_MEMBER_READ,
        CAP_MEMBER_WRITE,
        CAP_ASSISTANT_READ,
        CAP_ASSISTANT_WRITE,
        CAP_POLICY_READ,
        CAP_POLICY_WRITE,
        CAP_POLICY_EVALUATE,
        CAP_KNOWLEDGE_READ,
        CAP_KNOWLEDGE_WRITE,
        CAP_APIKEY_READ,
        CAP_APIKEY_WRITE,
        CAP_CONVERSATION_READ,
        CAP_CONVERSATION_DELETE,
        CAP_CHAT_WRITE,
        CAP_USAGE_READ,
        CAP_AUDIT_READ,
        CAP_BILLING_READ,
        CAP_CHANNEL_READ,
        CAP_CHANNEL_WRITE,
    }
)

_READ_ONLY_CAPS = frozenset(
    {
        CAP_ORG_READ,
        CAP_ASSISTANT_READ,
        CAP_POLICY_READ,
        CAP_KNOWLEDGE_READ,
        CAP_CONVERSATION_READ,
        CAP_APIKEY_READ,
        CAP_CHANNEL_READ,
    }
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    OrganizationMembership.Role.READ_ONLY: _READ_ONLY_CAPS,
    OrganizationMembership.Role.MEMBER: _READ_ONLY_CAPS | {CAP_USAGE_READ, CAP_BILLING_READ, CAP_CHAT_WRITE},
    OrganizationMembership.Role.DEVELOPER: ALL_CAPABILITIES
    - {CAP_ORG_DELETE, CAP_MEMBER_WRITE, CAP_AUDIT_READ, CAP_ORG_WRITE},
    OrganizationMembership.Role.ADMIN: ALL_CAPABILITIES - {CAP_ORG_DELETE},
    OrganizationMembership.Role.OWNER: ALL_CAPABILITIES,
}

ROLE_RANK = {
    OrganizationMembership.Role.READ_ONLY: 10,
    OrganizationMembership.Role.MEMBER: 20,
    OrganizationMembership.Role.DEVELOPER: 30,
    OrganizationMembership.Role.ADMIN: 40,
    OrganizationMembership.Role.OWNER: 50,
}


def role_capabilities(role: str) -> frozenset[str]:
    return ROLE_CAPABILITIES.get(role, frozenset())


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant for a single request."""

    organization: Organization
    role: str
    membership: OrganizationMembership | None = None
    api_key: object | None = None
    actor_user: "User | None" = None
    scopes: frozenset[str] = frozenset()

    @property
    def is_api_key(self) -> bool:
        return self.api_key is not None

    @property
    def capabilities(self) -> frozenset[str]:
        if self.api_key is not None:
            return ALL_CAPABILITIES if "*" in self.scopes else self.scopes
        return role_capabilities(self.role)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def can_edit(self) -> bool:
        return self.can(CAP_ASSISTANT_WRITE)

    @property
    def is_admin(self) -> bool:
        if self.api_key is not None:
            return self.can(CAP_MEMBER_WRITE)
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[OrganizationMembership.Role.ADMIN]


def _organization_from_request(request) -> str | None:
    header = request.headers.get("X-Organization-Id")
    if header:
        return header.strip()
    return None


def resolve_tenant(request, *, organization_id: str | None = None, required: bool = True) -> TenantContext | None:
    """Resolve and authorise the tenant for ``request``.

    * API-key callers are always bound to the key's organization; a conflicting
      ``X-Organization-Id`` is rejected instead of being trusted.
    * User callers must have an explicit membership; a supplied id is never
      trusted without that check.
    """
    from apps.api_keys.principal import APIKeyPrincipal

    auth = getattr(request, "auth", None)
    if isinstance(auth, APIKeyPrincipal):
        key = auth.api_key
        requested = organization_id or _organization_from_request(request)
        if requested and str(requested) != str(key.organization_id):
            raise TenantAccessDenied("Resource not found.")
        organization = key.organization
        if not organization.is_active:
            raise APIError(
                "This organization is not active.", code="organization_inactive", extra={"status": organization.status}
            )
        return TenantContext(
            organization=organization,
            role="API_KEY",
            api_key=key,
            actor_user=None,
            scopes=frozenset(key.scopes or []),
        )

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        from rest_framework.exceptions import NotAuthenticated

        raise NotAuthenticated("Authentication credentials were not provided.")

    requested = organization_id or _organization_from_request(request)
    queryset = OrganizationMembership.objects.select_related("organization").filter(user=user)

    if requested:
        membership = queryset.filter(organization_id=requested).first()
        if membership is None:
            raise TenantAccessDenied("Resource not found.")
    else:
        memberships = list(queryset[:2])
        if not memberships:
            if required:
                raise APIError(
                    "You do not belong to any organization yet.",
                    code="no_organization",
                    extra=None,
                )
            return None
        if len(memberships) > 1 and required:
            raise APIError(
                "Multiple organizations available. Send the X-Organization-Id header to select one.",
                code="organization_required",
            )
        membership = memberships[0]

    organization = membership.organization
    if not organization.is_active:
        raise APIError(
            "This organization is not active.", code="organization_inactive", extra={"status": organization.status}
        )
    return TenantContext(
        organization=organization,
        role=membership.role,
        membership=membership,
        actor_user=user,
    )


def get_tenant(request) -> TenantContext:
    """Cached tenant resolution for the current request."""
    cached = getattr(request, "tenant", None)
    if cached is not None:
        return cached
    tenant = resolve_tenant(request)
    request.tenant = tenant
    return tenant  # type: ignore[return-value]


def get_optional_tenant(request) -> TenantContext | None:
    cached = getattr(request, "tenant", None)
    if cached is not None:
        return cached
    tenant = resolve_tenant(request, required=False)
    request.tenant = tenant
    return tenant

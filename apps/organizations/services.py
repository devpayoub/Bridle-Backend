"""Organization and membership services."""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils.text import slugify

from apps.billing.services import assert_can_add_member, ensure_subscription
from apps.common.exceptions import APIError
from apps.organizations.models import Organization, OrganizationMembership

logger = logging.getLogger("apps.organizations")


def unique_organization_slug(name: str) -> str:
    base = slugify(name)[:180] or "organization"
    candidate = base
    suffix = 2
    while Organization.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@transaction.atomic
def create_organization(*, owner, name: str, timezone: str = "UTC") -> Organization:
    organization = Organization.objects.create(
        name=name.strip()[:200],
        slug=unique_organization_slug(name),
        timezone=timezone or "UTC",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=owner,
        role=OrganizationMembership.Role.OWNER,
    )
    ensure_subscription(organization)

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.ORGANIZATION_CREATED,
        organization=organization,
        actor_user=owner,
        target_type="organization",
        target_id=organization.id,
        metadata={"name": organization.name, "slug": organization.slug},
    )
    logger.info("Organization created id=%s owner=%s", organization.id, owner.id)
    return organization


@transaction.atomic
def update_organization(organization: Organization, *, data: dict, actor=None) -> Organization:
    changed = []
    for field in ("name", "timezone"):
        if field in data and data[field] != getattr(organization, field):
            setattr(organization, field, data[field])
            changed.append(field)
    if changed:
        organization.save()
        from apps.audit.models import AuditLog
        from apps.audit.services import record_event

        record_event(
            action=AuditLog.Action.ORGANIZATION_UPDATED,
            organization=organization,
            actor_user=actor,
            target_type="organization",
            target_id=organization.id,
            metadata={"changes": changed},
        )
    return organization


def _owner_count(organization: Organization) -> int:
    return OrganizationMembership.objects.filter(
        organization=organization, role=OrganizationMembership.Role.OWNER
    ).count()


@transaction.atomic
def add_member(*, organization: Organization, email: str, role: str, actor=None) -> OrganizationMembership:
    from apps.accounts.models import User

    assert_can_add_member(organization, current_count=organization.memberships.count())

    user = User.objects.filter(email=email.strip().lower(), is_active=True).first()
    if user is None:
        raise APIError(
            "No active account exists for this email address. Ask the user to register first.",
            code="user_not_found",
        )
    if OrganizationMembership.objects.filter(organization=organization, user=user).exists():
        raise APIError("This user is already a member of the organization.", code="already_member")
    if role == OrganizationMembership.Role.OWNER:
        raise APIError(
            "Ownership is transferred explicitly, not assigned when adding a member.",
            code="invalid_role",
        )

    membership = OrganizationMembership.objects.create(
        organization=organization, user=user, role=role, invited_by=actor
    )

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.MEMBER_ADDED,
        organization=organization,
        actor_user=actor,
        target_type="membership",
        target_id=membership.id,
        metadata={"email": user.email, "role": role},
    )
    return membership


@transaction.atomic
def update_member_role(*, membership: OrganizationMembership, role: str, actor=None) -> OrganizationMembership:
    organization = membership.organization
    previous = membership.role
    if previous == role:
        return membership

    if previous == OrganizationMembership.Role.OWNER:
        if _owner_count(organization) <= 1 and role != OrganizationMembership.Role.OWNER:
            raise APIError(
                "An organization must keep at least one owner.", code="last_owner"
            )

    membership.role = role
    membership.save(update_fields=["role", "updated_at"])

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.MEMBER_ROLE_CHANGED,
        organization=organization,
        actor_user=actor,
        target_type="membership",
        target_id=membership.id,
        metadata={"previous_role": previous, "role": role, "email": membership.user.email},
    )
    return membership


@transaction.atomic
def remove_member(*, membership: OrganizationMembership, actor=None) -> None:
    organization = membership.organization
    if membership.role == OrganizationMembership.Role.OWNER and _owner_count(organization) <= 1:
        raise APIError("An organization must keep at least one owner.", code="last_owner")

    email = membership.user.email
    membership_id = membership.id
    membership.delete()

    from apps.audit.models import AuditLog
    from apps.audit.services import record_event

    record_event(
        action=AuditLog.Action.MEMBER_REMOVED,
        organization=organization,
        actor_user=actor,
        target_type="membership",
        target_id=membership_id,
        metadata={"email": email},
    )


# ---------------------------------------------------------------------------
# Workspace AI provider settings
# ---------------------------------------------------------------------------
def get_ai_settings(organization) -> "OrganizationAISettings | None":
    from apps.organizations.models import OrganizationAISettings

    return OrganizationAISettings.objects.filter(organization=organization).first()


def describe_ai_settings(organization) -> dict:
    """Everything the console may know about the workspace's AI configuration.

    The API key itself is never included — only whether one exists and its last
    four characters.
    """
    from django.conf import settings

    from apps.ai_gateway.registry import available_providers, provider_labels

    row = get_ai_settings(organization)
    env_key = bool(settings.AI_PROVIDER_SETTINGS.get("openai", {}).get("api_key"))

    provider = (row.provider if row and row.provider else settings.AI_DEFAULT_PROVIDER)
    model = (row.model if row and row.model else "")

    return {
        "provider": provider,
        "model": model,
        "has_key": bool(row and row.has_api_key),
        "key_last4": row.api_key_last4 if row else "",
        "source": "workspace" if row and row.has_api_key else "environment",
        "available_providers": available_providers(),
        "provider_labels": provider_labels(),
        "environment": {
            "provider": settings.AI_DEFAULT_PROVIDER,
            "model": settings.AI_DEFAULT_MODEL_NAME,
            "has_key": env_key,
        },
        "updated_at": row.updated_at if row else None,
        "updated_by": row.updated_by.email if row and row.updated_by_id else None,
    }


def update_ai_settings(*, organization, data: dict, actor=None, request=None) -> dict:
    """Create or update the workspace provider configuration.

    An empty/absent ``api_key`` keeps the stored one; passing ``""`` explicitly
    clears it (the console uses DELETE for that, so PUT stays additive).
    """
    from apps.ai_gateway.registry import available_providers
    from apps.audit.models import AuditLog
    from apps.audit.services import record_event
    from apps.organizations.models import OrganizationAISettings

    provider = data.get("provider")
    if provider and provider not in available_providers():
        raise APIError("Unknown AI provider.", code="invalid_provider")

    row, _ = OrganizationAISettings.objects.get_or_create(organization=organization)
    if provider:
        row.provider = provider
    if "model" in data:
        row.model = (data.get("model") or "").strip()
    if data.get("api_key"):
        row.set_api_key(data["api_key"])
    row.updated_by = actor if getattr(actor, "pk", None) else None
    row.save()

    record_event(
        action=AuditLog.Action.AI_SETTINGS_UPDATED,
        organization=organization,
        actor_user=actor,
        target_type="organization",
        target_id=organization.id,
        metadata={"provider": row.provider, "model": row.model, "key_last4": row.api_key_last4},
        request=request,
    )
    return describe_ai_settings(organization)


def clear_ai_settings(*, organization, actor=None, request=None) -> None:
    """Remove stored credentials and fall back to the environment defaults."""
    from apps.audit.models import AuditLog
    from apps.audit.services import record_event
    from apps.organizations.models import OrganizationAISettings

    OrganizationAISettings.objects.filter(organization=organization).delete()
    record_event(
        action=AuditLog.Action.AI_SETTINGS_CLEARED,
        organization=organization,
        actor_user=actor,
        target_type="organization",
        target_id=organization.id,
        request=request,
    )


def resolve_provider(organization):
    """Provider adapter + model for this workspace.

    Order of precedence: stored workspace credential → environment default. The
    returned source is surfaced in the console so operators can tell which key is
    actually in use.
    """
    from django.conf import settings

    from apps.ai_gateway.registry import get_provider

    row = get_ai_settings(organization)
    api_key = row.get_api_key() if row and row.has_api_key else ""
    name = (row.provider if row and row.provider else settings.AI_DEFAULT_PROVIDER)
    model = (row.model if row and row.model else settings.AI_DEFAULT_MODEL_NAME)
    provider = get_provider(name, api_key=api_key or None)
    return provider, model, ("workspace" if api_key else "environment")


def test_ai_settings(organization) -> dict:
    """Make one tiny generation call to prove the configured credential works.

    Never raises: the console needs a structured result either way, and a failed
    probe must not look like a server error.
    """
    import time

    from apps.ai_gateway.base import GenerationRequest, ProviderError, ProviderMessage

    try:
        provider, model, source = resolve_provider(organization)
    except Exception as exc:  # noqa: BLE001 - misconfiguration is reported, not raised
        return {"ok": False, "provider": "", "model": "", "source": "", "error": str(exc)[:200]}

    started = time.monotonic()
    try:
        result = provider.generate_response(
            GenerationRequest(
                model=model,
                messages=[ProviderMessage(role="user", content="ping")],
                max_output_tokens=16,
                temperature=0.0,
            )
        )
    except ProviderError as exc:
        return {
            "ok": False,
            "provider": provider.name,
            "model": model,
            "source": source,
            "error": str(exc)[:200],
            "error_code": exc.code,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    return {
        "ok": True,
        "provider": provider.name,
        "model": result.model or model,
        "source": source,
        "latency_ms": result.latency_ms or int((time.monotonic() - started) * 1000),
        "sample": result.text[:120],
    }

"""Serializers for the platform administration surface.

All read-only except the two status toggles: a platform admin can suspend a
workspace or deactivate an account, but cannot rewrite tenant content from here.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.assistants.models import Assistant
from apps.audit.models import AuditLog
from apps.organizations.models import Organization, OrganizationMembership
from apps.accounts.models import User


class PlatformOrganizationSerializer(serializers.ModelSerializer):
    plan_code = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    assistant_count = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = (
            "id",
            "name",
            "slug",
            "status",
            "timezone",
            "plan_code",
            "member_count",
            "assistant_count",
            "owner_email",
            "created_at",
        )
        read_only_fields = fields

    def get_plan_code(self, obj) -> str | None:
        return obj.plan.code if obj.plan else None

    def get_member_count(self, obj) -> int:
        return getattr(obj, "member_count", 0)

    def get_assistant_count(self, obj) -> int:
        return getattr(obj, "assistant_count", 0)

    def get_owner_email(self, obj) -> str | None:
        return getattr(obj, "owner_email", None)


class PlatformOrganizationUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Organization.Status.choices)


class PlatformUserSerializer(serializers.ModelSerializer):
    organization_count = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "is_active",
            "is_staff",
            "is_superuser",
            "organization_count",
            "last_login",
            "date_joined",
        )
        read_only_fields = fields

    def get_name(self, obj) -> str:
        # The custom user model stores `full_name`; it is not an AbstractUser, so
        # there is no get_full_name() to fall back on.
        return (obj.full_name or "").strip() or obj.email.split("@")[0]

    def get_organization_count(self, obj) -> int:
        return getattr(obj, "organization_count", 0)


class PlatformUserUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_superuser = serializers.BooleanField(required=False)


class PlatformAssistantSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_id = serializers.UUIDField(source="organization.id", read_only=True)

    class Meta:
        model = Assistant
        fields = (
            "id",
            "name",
            "organization_id",
            "organization_name",
            "status",
            "domain_type",
            "model_provider",
            "model_name",
            "created_at",
        )
        read_only_fields = fields


class PlatformAuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor_user.email", read_only=True, default=None)
    organization_name = serializers.CharField(source="organization.name", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "actor_email",
            "organization_name",
            "target_type",
            "target_id",
            "metadata",
            "request_id",
            "created_at",
        )
        read_only_fields = fields


class PlatformMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ("id", "user_id", "user_email", "role", "created_at")
        read_only_fields = fields

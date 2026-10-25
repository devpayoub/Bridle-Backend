"""Organization serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.organizations.models import Organization, OrganizationMembership


class OrganizationSerializer(serializers.ModelSerializer):
    plan_code = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

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
            "role",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_plan_code(self, obj) -> str | None:
        subscription = getattr(obj, "subscription", None)
        return subscription.plan.code if subscription else None

    def get_member_count(self, obj) -> int:
        annotated = getattr(obj, "member_count", None)
        return annotated if annotated is not None else obj.memberships.count()

    def get_role(self, obj) -> str | None:
        roles = self.context.get("roles") or {}
        if str(obj.id) in roles:
            return roles[str(obj.id)]
        membership = getattr(obj, "_viewer_membership", None)
        return membership.role if membership else None


class OrganizationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    timezone = serializers.CharField(max_length=64, required=False, default="UTC")


class OrganizationUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    timezone = serializers.CharField(max_length=64, required=False)


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ("id", "user_id", "email", "full_name", "role", "created_at")
        read_only_fields = fields


class MemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=[
            OrganizationMembership.Role.ADMIN,
            OrganizationMembership.Role.DEVELOPER,
            OrganizationMembership.Role.MEMBER,
            OrganizationMembership.Role.READ_ONLY,
        ],
        default=OrganizationMembership.Role.MEMBER,
    )


class MemberRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices)


class AISettingsUpdateSerializer(serializers.Serializer):
    """Write payload for workspace AI settings.

    ``api_key`` is write-only and optional: omitting it keeps the stored key,
    which is what the console does when only the model changes.
    """

    provider = serializers.CharField(max_length=32, required=False, allow_blank=True)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    api_key = serializers.CharField(max_length=400, required=False, allow_blank=True, write_only=True)

    def validate_provider(self, value):
        from apps.ai_gateway.registry import available_providers

        if value and value not in available_providers():
            raise serializers.ValidationError(f"Unknown provider. Choose one of: {', '.join(available_providers())}.")
        return value

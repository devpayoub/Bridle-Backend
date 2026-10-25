"""Account API serializers."""
from __future__ import annotations

from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.common.exceptions import APIError


class UserSerializer(serializers.ModelSerializer):
    is_email_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "full_name",
            "is_email_verified",
            "is_superuser",
            "date_joined",
            "is_active",
        )
        read_only_fields = fields


class MembershipSummarySerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    organization_name = serializers.CharField()
    organization_slug = serializers.CharField()
    role = serializers.CharField()


class MeSerializer(serializers.Serializer):
    user = UserSerializer()
    memberships = MembershipSummarySerializer(many=True)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, min_length=10, max_length=128, trim_whitespace=False)
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        # ``authenticate`` runs the configured backends and constant-time checks.
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"].strip().lower(),
            password=attrs["password"],
        )
        if user is None:
            from apps.common.exceptions import InvalidCredentials

            raise InvalidCredentials("Invalid email or password.")
        if not user.is_active:
            from apps.common.exceptions import InvalidCredentials

            raise InvalidCredentials("This account has been deactivated.")
        attrs["user"] = user
        return attrs


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    user = UserSerializer(read_only=True)

    @classmethod
    def for_user(cls, user: User) -> dict:
        refresh = RefreshToken.for_user(user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": UserSerializer(user).data,
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=256)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=256)
    new_password = serializers.CharField(min_length=10, max_length=128, trim_whitespace=False)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(min_length=10, max_length=128, trim_whitespace=False)

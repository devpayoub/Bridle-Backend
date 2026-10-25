"""Authentication API views. Views stay thin: validation in serializers, rules in services."""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from apps.common.exceptions import APIError
from apps.common.logging import set_log_context
from apps.common.serializers import DetailSerializer, MessageSerializer
from apps.usage.rate_limit import client_identifier, enforce_rate_limit, reset_rate_limit

logger = logging.getLogger("apps.accounts.views")


class RegisterView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=RegisterSerializer,
        responses={201: UserSerializer, 400: OpenApiResponse(description="Validation error")},
        summary="Create an account",
    )
    def post(self, request):
        enforce_rate_limit("auth.register", client_identifier(request))
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user, raw_token = services.register_user(
            email=data["email"], password=data["password"], full_name=data.get("full_name", "")
        )
        services.send_registration_email(user, raw_token)
        set_log_context(user_id=user.id)
        logger.info("User registered")
        return Response(
            {"user": UserSerializer(user).data, "detail": "Account created. Check your email to verify it."},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=LoginSerializer,
        responses={200: TokenPairSerializer, 401: OpenApiResponse(description="Invalid credentials")},
        summary="Obtain JWT access and refresh tokens",
    )
    def post(self, request):
        client_id = client_identifier(request)
        enforce_rate_limit("auth.login", client_id)
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        # Per-account throttle blunts distributed credential stuffing.
        enforce_rate_limit("auth.login", f"user:{user.id}")
        reset_rate_limit("auth.login", client_id)

        set_log_context(user_id=user.id)
        logger.info("User logged in")
        return Response(TokenPairSerializer.for_user(user), status=status.HTTP_200_OK)


class RefreshView(TokenRefreshView):
    """Rotating refresh tokens with the platform rate limit applied."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request, *args, **kwargs):
        enforce_rate_limit("auth.token_refresh", client_identifier(request))
        return super().post(request, *args, **kwargs)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Authentication"], request=LogoutSerializer, responses={204: None}, summary="Revoke a refresh token")
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_refresh = serializer.validated_data.get("refresh")
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                # Already expired/blacklisted: logout is idempotent.
                pass
        logger.info("User logged out")
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Authentication"], responses={200: MeSerializer}, summary="Current user profile")
    def get(self, request):
        from apps.organizations.models import OrganizationMembership

        memberships = (
            OrganizationMembership.objects.select_related("organization")
            .filter(user=request.user)
            .order_by("organization__name")
        )
        payload = {
            "user": request.user,
            "memberships": [
                {
                    "organization_id": m.organization_id,
                    "organization_name": m.organization.name,
                    "organization_slug": m.organization.slug,
                    "role": m.role,
                }
                for m in memberships
            ],
        }
        return Response(MeSerializer(payload).data)


class VerifyEmailView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["Authentication"], request=VerifyEmailSerializer, responses={200: MessageSerializer}, summary="Confirm an email address")
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.verify_email(serializer.validated_data["token"])
        return Response({"detail": "Email verified.", "user": UserSerializer(user).data})


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Authentication"], request=None, responses={200: DetailSerializer}, summary="Resend the verification email")
    def post(self, request):
        enforce_rate_limit("auth.password_reset", f"user:{request.user.id}")
        services.resend_verification(request.user)
        return Response({"detail": "Verification email sent."})


class PasswordResetRequestView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Authentication"],
        request=PasswordResetRequestSerializer,
        responses={202: DetailSerializer},
        summary="Request a password reset link",
        description="Always returns 202 to avoid disclosing whether an address is registered.",
    )
    def post(self, request):
        enforce_rate_limit("auth.password_reset", client_identifier(request))
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(serializer.validated_data["email"])
        return Response(
            {"detail": "If the address is registered, a reset link has been sent."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetConfirmView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["Authentication"], request=PasswordResetConfirmSerializer, responses={200: DetailSerializer}, summary="Complete a password reset")
    def post(self, request):
        enforce_rate_limit("auth.password_reset", client_identifier(request))
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_password_reset(
            serializer.validated_data["token"], serializer.validated_data["new_password"]
        )
        return Response({"detail": "Password updated. Sign in with your new password."})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Authentication"], request=ChangePasswordSerializer, responses={200: DetailSerializer}, summary="Change password")
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(
            request.user,
            serializer.validated_data["current_password"],
            serializer.validated_data["new_password"],
        )
        return Response({"detail": "Password updated. Please sign in again."})


class DeactivateAccountView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Authentication"], request=None, responses={202: DetailSerializer}, summary="Deactivate the current account")
    def post(self, request):
        services.deactivate_account(request.user)
        return Response({"detail": "Account deactivated."}, status=status.HTTP_202_ACCEPTED)

from django.urls import path

from apps.accounts.views import (
    ChangePasswordView,
    DeactivateAccountView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RefreshView,
    RegisterView,
    ResendVerificationView,
    VerifyEmailView,
)

app_name = "accounts"

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/token/refresh/", RefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/verify-email/resend/", ResendVerificationView.as_view(), name="verify-email-resend"),
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("auth/password-change/", ChangePasswordView.as_view(), name="password-change"),
    path("auth/deactivate/", DeactivateAccountView.as_view(), name="deactivate"),
]

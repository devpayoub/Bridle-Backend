"""URLs for the platform administration API (all superuser-only)."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.platform_admin.views import (
    PlatformAssistantViewSet,
    PlatformAuditLogViewSet,
    PlatformOrganizationViewSet,
    PlatformOverviewView,
    PlatformUserViewSet,
)

router = DefaultRouter()
router.register("organizations", PlatformOrganizationViewSet, basename="platform-organization")
router.register("users", PlatformUserViewSet, basename="platform-user")
router.register("assistants", PlatformAssistantViewSet, basename="platform-assistant")
router.register("audit-logs", PlatformAuditLogViewSet, basename="platform-audit-log")

urlpatterns = [
    path("platform/overview/", PlatformOverviewView.as_view(), name="platform-overview"),
    path("platform/", include(router.urls)),
]

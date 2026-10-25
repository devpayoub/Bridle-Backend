"""Root URL configuration.

All application endpoints live under ``/api/v1/``. Documentation is exposed only
when ``EXPOSE_API_DOCS`` is enabled (development by default, opt-in in production).
"""
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.common.views import HealthView, ReadyView


def _not_found(_request, *_args, **_kwargs):
    return JsonResponse(
        {"error": {"code": "not_found", "message": "Resource not found."}}, status=404
    )


api_v1_patterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.organizations.urls")),
    path("", include("apps.api_keys.urls")),
    path("", include("apps.assistants.urls")),
    path("", include("apps.policies.urls")),
    path("", include("apps.knowledge.urls")),
    path("", include("apps.conversations.urls")),
    path("", include("apps.usage.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.platform_admin.urls")),
    path("health/", HealthView.as_view(), name="health"),
    path("ready/", ReadyView.as_view(), name="ready"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include((api_v1_patterns, "v1"))),
]

if settings.EXPOSE_API_DOCS:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

handler404 = _not_found

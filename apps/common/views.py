"""Operational health endpoints.

These endpoints are intentionally unauthenticated but expose no secrets,
versions of internal libraries, configuration values or stack traces.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from apps.common.serializers import HealthSerializer, ReadySerializer
from rest_framework.views import APIView

logger = logging.getLogger("apps.common.health")


class HealthView(APIView):
    """Liveness probe: the process is up and able to serve requests."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []

    @extend_schema(tags=["System"], summary="Liveness probe", responses={200: HealthSerializer})
    def get(self, request):
        return Response({"status": "ok", "service": "assistant-platform-api"})


class ReadyView(APIView):
    """Readiness probe: dependencies required to serve traffic are reachable."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []

    @extend_schema(tags=["System"], summary="Readiness probe", responses={200: ReadySerializer, 503: ReadySerializer})
    def get(self, request):
        checks: dict[str, str] = {}

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception:
            logger.exception("Readiness check failed for database")
            checks["database"] = "failed"

        try:
            cache.set("readiness-probe", "1", timeout=5)
            checks["cache"] = "ok" if cache.get("readiness-probe") == "1" else "degraded"
        except Exception:
            logger.exception("Readiness check failed for cache")
            checks["cache"] = "failed"

        healthy = checks["database"] == "ok"
        status_code = 200 if healthy else 503
        payload = {
            "status": "ready" if healthy else "unavailable",
            "checks": checks,
            "vector_search": "enabled" if settings.VECTOR_SEARCH_ENABLED else "disabled",
        }
        return Response(payload, status=status_code)

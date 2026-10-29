"""Channel management URLs."""
from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.channels.views import ChannelViewSet

app_name = "channels"

router = DefaultRouter()
router.register("channels", ChannelViewSet, basename="channel")

urlpatterns = [
    path("", include(router.urls)),
]

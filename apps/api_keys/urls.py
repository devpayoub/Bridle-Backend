from rest_framework.routers import DefaultRouter

from apps.api_keys.views import APIKeyViewSet

app_name = "api_keys"

router = DefaultRouter(trailing_slash=True)
router.register("api-keys", APIKeyViewSet, basename="api-key")

urlpatterns = router.urls

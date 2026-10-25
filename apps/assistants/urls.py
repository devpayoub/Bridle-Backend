from rest_framework.routers import DefaultRouter

from apps.assistants.views import AssistantViewSet

app_name = "assistants"

router = DefaultRouter(trailing_slash=True)
router.register("assistants", AssistantViewSet, basename="assistant")

urlpatterns = router.urls

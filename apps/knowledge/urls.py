from rest_framework.routers import DefaultRouter

from apps.knowledge.views import DocumentViewSet, KnowledgeBaseViewSet

app_name = "knowledge"

router = DefaultRouter(trailing_slash=True)
router.register("knowledge-bases", KnowledgeBaseViewSet, basename="knowledge-base")
router.register("documents", DocumentViewSet, basename="document")

urlpatterns = router.urls

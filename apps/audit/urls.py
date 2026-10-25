from rest_framework.routers import DefaultRouter

from apps.audit.views import AuditLogViewSet

app_name = "audit"

router = DefaultRouter(trailing_slash=True)
router.register("audit-logs", AuditLogViewSet, basename="audit-log")

urlpatterns = router.urls

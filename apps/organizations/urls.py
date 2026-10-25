from rest_framework.routers import DefaultRouter

from apps.organizations.views import OrganizationViewSet

app_name = "organizations"

router = DefaultRouter(trailing_slash=True)
router.register("organizations", OrganizationViewSet, basename="organization")

urlpatterns = router.urls

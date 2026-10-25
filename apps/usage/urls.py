from django.urls import path

from apps.usage.views import UsageByApiKeyView, UsageByAssistantView, UsageSummaryView

app_name = "usage"

urlpatterns = [
    path("usage/summary/", UsageSummaryView.as_view(), name="summary"),
    path("usage/by-assistant/", UsageByAssistantView.as_view(), name="by-assistant"),
    path("usage/by-api-key/", UsageByApiKeyView.as_view(), name="by-api-key"),
]

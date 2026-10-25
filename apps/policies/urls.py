from django.urls import path

from apps.policies.views import PolicyEvaluateRequestView, PolicyEvaluateResponseView

app_name = "policies"

urlpatterns = [
    path("policies/evaluate-request/", PolicyEvaluateRequestView.as_view(), name="evaluate-request"),
    path("policies/evaluate-response/", PolicyEvaluateResponseView.as_view(), name="evaluate-response"),
]

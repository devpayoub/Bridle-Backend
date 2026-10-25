from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.conversations.views import ChatCompletionsView, ConversationViewSet

app_name = "conversations"

router = DefaultRouter(trailing_slash=True)
router.register("conversations", ConversationViewSet, basename="conversation")

urlpatterns = [
    path("chat/completions/", ChatCompletionsView.as_view(), name="chat-completions"),
] + router.urls

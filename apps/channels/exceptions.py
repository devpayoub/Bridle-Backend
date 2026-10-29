"""Channel-specific errors."""
from __future__ import annotations

from rest_framework import status

from apps.common.exceptions import APIError


class ChannelRegistrationFailed(APIError):
    """The provider rejected the webhook registration (502)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "The messaging provider rejected the webhook registration."
    default_code = "channel_registration_failed"

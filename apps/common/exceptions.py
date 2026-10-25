"""Consistent error envelope for the whole API.

Every error response has the shape::

    {
      "error": {
        "code": "validation_error",
        "message": "Human readable summary",
        "details": {...},          # optional, field errors or metadata
        "request_id": "..."
      }
    }
"""
from __future__ import annotations

import logging
from typing import Any

from rest_framework import exceptions, status
from rest_framework.response import Response

from apps.common.logging import get_request_id

logger = logging.getLogger("apps.common.errors")


class APIError(exceptions.APIException):
    """Base class for domain errors carrying a stable machine-readable code."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Request could not be processed."
    default_code = "bad_request"

    def __init__(self, detail: Any = None, code: str | None = None, *, extra: dict | None = None):
        super().__init__(detail, code)
        # Keep the caller-supplied code as the stable machine-readable code.
        if code:
            self.default_code = code
        self.extra = extra or {}


class InvalidCredentials(APIError):
    """401 without requiring an authentication class to supply a WWW-Authenticate header."""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid email or password."
    default_code = "authentication_failed"


class Conflict(APIError):
    """409 for state conflicts (deleting a key that is still active, …)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class RateLimitExceeded(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Rate limit exceeded. Retry later."
    default_code = "rate_limited"

    def __init__(self, detail: Any = None, *, retry_after: int = 60, scope: str | None = None):
        super().__init__(detail, extra={"scope": scope} if scope else None)
        self.retry_after = max(1, int(retry_after))


class QuotaExceeded(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Your plan's usage quota has been exhausted for this period."
    default_code = "quota_exceeded"


class EntitlementExceeded(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Your plan does not permit this operation."
    default_code = "entitlement_exceeded"


class TenantAccessDenied(APIError):
    """Raised when a caller references an object outside their organization.

    Deliberately maps to 404 so resource existence is not disclosed.
    """

    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Resource not found."
    default_code = "not_found"


class AssistantNotAvailable(APIError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This assistant is not accepting requests."
    default_code = "assistant_not_available"


class ProviderUnavailable(APIError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "The AI provider is temporarily unavailable. Please retry."
    default_code = "provider_unavailable"


class IdempotencyConflict(APIError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This Idempotency-Key was already used with a different request body."
    default_code = "idempotency_conflict"


_DRF_CODE_MAP = {
    "not_authenticated": ("not_authenticated", "Authentication credentials were not provided."),
    "authentication_failed": ("authentication_failed", "Invalid credentials."),
    "permission_denied": ("permission_denied", "You do not have permission to perform this action."),
    "not_found": ("not_found", "Resource not found."),
    "method_not_allowed": ("method_not_allowed", "Method not allowed."),
    "throttled": ("rate_limited", "Rate limit exceeded. Retry later."),
    "parse_error": ("parse_error", "Malformed request body."),
    "unsupported_media_type": ("unsupported_media_type", "Unsupported content type."),
}


def _first_message(data: Any) -> str:
    if isinstance(data, dict):
        if "detail" in data:
            return _first_message(data["detail"])
        for value in data.values():
            return _first_message(value)
        return "Request could not be processed."
    if isinstance(data, (list, tuple)):
        return _first_message(data[0]) if data else "Request could not be processed."
    return str(data)


def api_exception_handler(exc, context):
    """DRF exception handler producing the platform error envelope."""
    # Imported lazily: rest_framework.views imports DRF settings, which import
    # this module's consumers (authentication classes) at startup.
    from rest_framework.views import exception_handler as drf_exception_handler

    request = context.get("request")
    request_id = getattr(request, "request_id", None) or get_request_id()

    response = drf_exception_handler(exc, context)

    if response is None:
        # Unhandled exception: log with correlation id, never leak internals.
        logger.exception("Unhandled exception path=%s", getattr(request, "path", "?"))
        return Response(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = getattr(exc, "default_code", "error")
    details: dict[str, Any] | None = None

    if isinstance(exc, exceptions.ValidationError):
        code = "validation_error"
        details = response.data if isinstance(response.data, dict) else {"errors": response.data}
    elif isinstance(exc, APIError):
        code = getattr(exc, "default_code", code)
        if getattr(exc, "extra", None):
            details = exc.extra
    elif isinstance(exc, exceptions.Throttled):
        code = "rate_limited"
    else:
        code = _DRF_CODE_MAP.get(code, (code,))[0]

    message = _first_message(response.data)
    if isinstance(exc, APIError) and isinstance(exc.detail, str):
        message = exc.detail

    payload: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        payload["details"] = details

    response.data = {"error": payload}

    # Preserve protocol headers (e.g. WWW-Authenticate) and add Retry-After.
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response["Retry-After"] = str(int(retry_after))
    elif isinstance(exc, exceptions.Throttled) and exc.wait is not None:
        response["Retry-After"] = str(int(exc.wait))

    return response

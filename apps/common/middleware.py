"""Request correlation and latency instrumentation."""
from __future__ import annotations

import logging
import re
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

from apps.common.logging import reset_request_id, set_request_id

logger = logging.getLogger("apps.common.requests")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


class RequestContextMiddleware(MiddlewareMixin):
    """Assign a correlation id to every request and record its latency.

    The id is echoed back in the ``X-Request-ID`` response header so clients can
    quote it in support requests and operators can correlate log lines.
    """

    def process_request(self, request) -> None:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        request._request_id_token = set_request_id(request_id)
        request._start_time = time.monotonic()

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id:
            response["X-Request-ID"] = request_id
        start = getattr(request, "_start_time", None)
        if start is not None:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            response["X-Response-Time-Ms"] = f"{duration_ms}"
            if request.path.startswith("/api/"):
                logger.info(
                    "request.completed method=%s path=%s status=%s duration_ms=%s",
                    request.method,
                    request.path,
                    response.status_code,
                    duration_ms,
                    extra={"http_status": response.status_code, "duration_ms": duration_ms},
                )
        token = getattr(request, "_request_id_token", None)
        if token is not None:
            self._reset_token(request)
        return response

    def process_exception(self, request, exception):
        self._reset_token(request)
        return None

    @staticmethod
    def _reset_token(request) -> None:
        """Reset the context var exactly once.

        Both `process_response` and `process_exception` run for a failing view, so
        the token has to be cleared here — resetting it twice raises
        "Token has already been used once" and hides the original exception.
        """
        token = getattr(request, "_request_id_token", None)
        if token is None:
            return
        request._request_id_token = None
        reset_request_id(token)

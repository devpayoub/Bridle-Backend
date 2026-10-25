"""Production settings: secure transport, explicit hosts, no debug surfaces."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

if SECRET_KEY == "insecure-development-key-change-me":  # noqa: F405
    raise ImproperlyConfigured(  # noqa: F821
        "DJANGO_SECRET_KEY must be set to a strong unique value in production."
    )

# ---------------------------------------------------------------------------
# Transport security
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# API documentation is private by default in production.
# ---------------------------------------------------------------------------
EXPOSE_API_DOCS = env.bool("EXPOSE_API_DOCS", default=False)  # noqa: F405

# Never leak stack traces through the API.
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]}  # noqa: F405

# CORS must be explicit in production.
if not CORS_ALLOWED_ORIGINS:  # noqa: F405
    raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must list the dashboard origins in production.")  # noqa: F821

LOGGING["handlers"]["console"]["formatter"] = "json"  # noqa: F405

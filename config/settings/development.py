"""Local development settings: verbose, permissive transport, never for production."""
from .base import *  # noqa: F401,F403

# True for local work, but a tunnelled instance must run with DJANGO_DEBUG=False:/n# the technical error page prints local variables, which can contain credentials.
DEBUG = env.bool("DJANGO_DEBUG", default=True)  # noqa: F405
ALLOWED_HOSTS = ["*"]
# On by default for local work, but overridable: an instance reachable from the
# internet should not publish its endpoint map.
EXPOSE_API_DOCS = env.bool("EXPOSE_API_DOCS", default=True)  # noqa: F405

# Console email is the default in development; tokens appear in the runserver log.
EMAIL_BACKEND = env("EMAIL_BACKEND")  # noqa: F405

# Relaxed transport security is acceptable only because DEBUG=True.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Dev convenience: allow the local SPA origins without extra configuration.
CORS_ALLOW_ALL_ORIGINS = False
if not CORS_ALLOWED_ORIGINS:  # noqa: F405
    CORS_ALLOWED_ORIGINS = [  # noqa: F405
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

LOGGING["formatters"]["plain"]["format"] = (  # noqa: F405
    "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s"
)
LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405

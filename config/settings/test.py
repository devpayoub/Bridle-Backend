"""
Test settings.

Tests run against a real PostgreSQL instance (the same engine used in
production) because the application relies on PostgreSQL full-text search and
pgvector. Redis is replaced by an in-process cache so the suite has no external
service dependency beyond the database.
"""
from .base import *  # noqa: F401,F403

DEBUG = False
TESTING = True
EXPOSE_API_DOCS = True

SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["*"]

# Fast hashing keeps the suite quick without weakening production settings.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

DATABASES["default"]["TEST"] = {"NAME": env("TEST_DATABASE_NAME", default="test_assistant_platform")}  # noqa: F405

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-cache"}}

# Manifest static storage requires collectstatic; tests use plain storage.
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405

# WhiteNoise is not exercised by the test suite.
MIDDLEWARE = [middleware for middleware in MIDDLEWARE if "whitenoise" not in middleware]  # noqa: F405

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Deterministic providers: no paid API calls in CI.
AI_DEFAULT_PROVIDER = "mock"
AI_DEFAULT_MODEL_NAME = "mock-model-1"
EMBEDDING_PROVIDER = "mock"
VECTOR_SEARCH_ENABLED = False
POLICY_MODEL_EVALUATOR_ENABLED = False

RATE_LIMIT_ENABLED = True
CHAT_IDEMPOTENCY_TTL_HOURS = 1

LOGGING["root"]["level"] = "ERROR"  # noqa: F405
LOGGING["loggers"]["apps"]["level"] = "ERROR"  # noqa: F405

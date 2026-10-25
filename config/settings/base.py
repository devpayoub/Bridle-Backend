"""
Base settings shared by every environment.

Configuration is entirely environment driven (12-factor).  No secret is ever
committed to the repository: ``.env`` is local-only and ``.env.example`` holds
placeholders.
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, "insecure-development-key-change-me"),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "testserver"]),
    DJANGO_TIME_ZONE=(str, "UTC"),
    DJANGO_LANGUAGE_CODE=(str, "en-us"),
    DATABASE_URL=(str, "postgres://postgres:postgres@localhost:5432/assistant_platform"),
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/1"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/2"),
    CORS_ALLOWED_ORIGINS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    MEDIA_ROOT=(str, str(BASE_DIR / "media")),
    EMAIL_BACKEND=(str, "django.core.mail.backends.console.EmailBackend"),
    EMAIL_FROM=(str, "no-reply@assistant-platform.local"),
    FRONTEND_BASE_URL=(str, "http://localhost:3000"),
    AI_DEFAULT_PROVIDER=(str, "mock"),
    AI_REQUEST_TIMEOUT=(int, 30),
    AI_MAX_RETRIES=(int, 2),
    EMBEDDING_PROVIDER=(str, "mock"),
    EMBEDDING_MODEL=(str, "text-embedding-3-small"),
    EMBEDDING_DIMENSIONS=(int, 1536),
    VECTOR_SEARCH_ENABLED=(bool, False),
    POLICY_MODEL_EVALUATOR_ENABLED=(bool, False),
    POLICY_FAIL_MODE=(str, "closed"),
    KNOWLEDGE_MAX_FILE_BYTES=(int, 10 * 1024 * 1024),
    KNOWLEDGE_SEARCH_CONFIG=(str, "english"),
    CHAT_MAX_MESSAGE_CHARS=(int, 8000),
    CHAT_HISTORY_MAX_MESSAGES=(int, 20),
    CHAT_MAX_OUTPUT_TOKENS=(int, 800),
    CHAT_REQUEST_TIMEOUT=(int, 45),
    LOG_LEVEL=(str, "INFO"),
    LOG_FORMAT=(str, "json"),
    EXPOSE_API_DOCS=(bool, False),
    AI_CREDENTIALS_KEY=(str, ""),
    AI_PROVIDER_LABEL=(str, ""),
)

# Read .env if present (never required in production; real deployments inject env vars).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "pgvector.django",
]

LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.organizations",
    "apps.api_keys",
    "apps.audit",
    "apps.platform_admin",
    "apps.billing",
    "apps.assistants",
    "apps.policies",
    "apps.knowledge",
    "apps.ai_gateway",
    "apps.conversations",
    "apps.usage",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.common.middleware.RequestContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["ATOMIC_REQUESTS"] = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ---------------------------------------------------------------------------
# Sessions / cookies / CSRF
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 8)
CSRF_COOKIE_HTTPONLY = False  # the SPA needs to read the token
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-request-id",
    "x-organization-id",
    "idempotency-key",
]

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api_keys.authentication.ApiKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Bridle AI API",
    "DESCRIPTION": (
        "Multi-tenant SaaS backend for building, governing and operating "
        "domain-restricted AI assistants.\n\n"
        "**Authentication**: dashboard endpoints accept JWT "
        "(`Authorization: Bearer <access_token>`) or session cookies. "
        "Server-to-server integrations use API keys with the same "
        "`Authorization: Bearer bb-...` header. Tenant selection uses the "
        "`X-Organization-Id` header (API keys are bound to their organization)."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": True,
    "SECURITY": [{"jwtAuth": []}, {"apiKeyAuth": []}],
}

# ---------------------------------------------------------------------------
# Caches / Celery
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "ap",
        "TIMEOUT": 300,
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 300
CELERY_TASK_TIME_LIMIT = 360
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

# Scheduled maintenance. Beat is only required because of these jobs.
from celery.schedules import crontab  # noqa: E402  (kept close to its only use)

CELERY_BEAT_SCHEDULE = {
    "purge-expired-conversations": {
        "task": "conversations.purge_expired",
        "schedule": crontab(hour=3, minute=0),
    },
    "purge-expired-audit-logs": {
        "task": "audit.purge_expired",
        "schedule": crontab(hour=3, minute=30),
    },
    "roll-up-usage-summaries": {
        "task": "usage.roll_up_summaries",
        "schedule": crontab(minute=15),
    },
}

# ---------------------------------------------------------------------------
# AI providers
# ---------------------------------------------------------------------------
AI_DEFAULT_PROVIDER = env("AI_DEFAULT_PROVIDER")
# Dedicated key for encrypting workspace provider credentials. Falls back to
# SECRET_KEY when unset; set it explicitly so rotating one does not break the other.
AI_CREDENTIALS_KEY = env("AI_CREDENTIALS_KEY", default="")
# Human label for the configured provider (e.g. the gateway vendor). Empty means
# the console falls back to a generic "OpenAI-compatible" label.
AI_PROVIDER_LABEL = env("AI_PROVIDER_LABEL", default="")
AI_DEFAULT_MODEL_NAME = env("AI_DEFAULT_MODEL_NAME", default="gpt-4o-mini")
AI_REQUEST_TIMEOUT = env("AI_REQUEST_TIMEOUT")
AI_MAX_RETRIES = env("AI_MAX_RETRIES")
AI_PROVIDER_SETTINGS = {
    "openai": {
        "api_key": env("OPENAI_API_KEY", default=""),
        "base_url": env("OPENAI_BASE_URL", default=""),
        "organization": env("OPENAI_ORGANIZATION", default=""),
    },
    "anthropic": {
        "api_key": env("ANTHROPIC_API_KEY", default=""),
        "base_url": env("ANTHROPIC_BASE_URL", default=""),
    },
}
EMBEDDING_PROVIDER = env("EMBEDDING_PROVIDER")
EMBEDDING_MODEL = env("EMBEDDING_MODEL")
EMBEDDING_DIMENSIONS = env("EMBEDDING_DIMENSIONS")
VECTOR_SEARCH_ENABLED = env("VECTOR_SEARCH_ENABLED")

# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------
POLICY_MODEL_EVALUATOR_ENABLED = env("POLICY_MODEL_EVALUATOR_ENABLED")
# "closed" => an evaluator failure produces a refusal instead of allowing traffic.
POLICY_FAIL_MODE = env("POLICY_FAIL_MODE")
POLICY_DEFAULT_ALLOW_THRESHOLD = env.float("POLICY_DEFAULT_ALLOW_THRESHOLD", default=0.60)
POLICY_DEFAULT_CLARIFY_THRESHOLD = env.float("POLICY_DEFAULT_CLARIFY_THRESHOLD", default=0.34)
POLICY_MAX_INPUT_CHARS = env.int("POLICY_MAX_INPUT_CHARS", default=8000)
POLICY_CLASSIFIER_MODEL = env("POLICY_CLASSIFIER_MODEL", default="gpt-4o-mini")
POLICY_MODEL_CONFIDENCE_THRESHOLD = env.float("POLICY_MODEL_CONFIDENCE_THRESHOLD", default=0.7)

# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------
MEDIA_ROOT = env("MEDIA_ROOT")
MEDIA_URL = "/media/"
KNOWLEDGE_MAX_FILE_BYTES = env("KNOWLEDGE_MAX_FILE_BYTES")
KNOWLEDGE_ALLOWED_EXTENSIONS = [".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".docx"]
KNOWLEDGE_SEARCH_CONFIG = env("KNOWLEDGE_SEARCH_CONFIG")
KNOWLEDGE_CHUNK_SIZE = env.int("KNOWLEDGE_CHUNK_SIZE", default=900)
KNOWLEDGE_CHUNK_OVERLAP = env.int("KNOWLEDGE_CHUNK_OVERLAP", default=120)
KNOWLEDGE_EMBEDDING_BATCH_SIZE = env.int("KNOWLEDGE_EMBEDDING_BATCH_SIZE", default=64)
KNOWLEDGE_RETRIEVAL_TOP_K = env.int("KNOWLEDGE_RETRIEVAL_TOP_K", default=5)

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
CHAT_MAX_MESSAGE_CHARS = env("CHAT_MAX_MESSAGE_CHARS")
CHAT_HISTORY_MAX_MESSAGES = env("CHAT_HISTORY_MAX_MESSAGES")
CHAT_MAX_OUTPUT_TOKENS = env("CHAT_MAX_OUTPUT_TOKENS")
CHAT_REQUEST_TIMEOUT = env("CHAT_REQUEST_TIMEOUT")
CHAT_IDEMPOTENCY_TTL_HOURS = env.int("CHAT_IDEMPOTENCY_TTL_HOURS", default=24)

# ---------------------------------------------------------------------------
# Rate limits: scope -> (max requests, window seconds)
# ---------------------------------------------------------------------------
RATE_LIMITS = {
    "auth.register": {"limit": 10, "window": 3600},
    "auth.login": {"limit": 20, "window": 300},
    "auth.password_reset": {"limit": 10, "window": 3600},
    "auth.token_refresh": {"limit": 60, "window": 300},
    "chat": {"limit": env.int("RATE_LIMIT_CHAT_PER_MINUTE", default=120), "window": 60},
    "policy.evaluate": {"limit": 300, "window": 60},
    "knowledge.upload": {"limit": 60, "window": 3600},
}
RATE_LIMIT_ENABLED = env.bool("RATE_LIMIT_ENABLED", default=True)

# ---------------------------------------------------------------------------
# Retention / privacy
# ---------------------------------------------------------------------------
CONVERSATION_RETENTION_DAYS = env.int("CONVERSATION_RETENTION_DAYS", default=365)
AUDIT_LOG_RETENTION_DAYS = env.int("AUDIT_LOG_RETENTION_DAYS", default=730)
USAGE_RETENTION_DAYS = env.int("USAGE_RETENTION_DAYS", default=730)

# ---------------------------------------------------------------------------
# Uploads / request limits
# ---------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MEMORY_SIZE", default=12 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env.int("FILE_UPLOAD_MAX_MEMORY_SIZE", default=12 * 1024 * 1024)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND")
EMAIL_FROM = env("EMAIL_FROM")
DEFAULT_FROM_EMAIL = EMAIL_FROM
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL")

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE")
TIME_ZONE = env("DJANGO_TIME_ZONE")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Logging (structured, redacted)
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL")
LOG_FORMAT = env("LOG_FORMAT")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {"()": "apps.common.logging.RequestContextFilter"},
        "redact": {"()": "apps.common.logging.SecretRedactionFilter"},
    },
    "formatters": {
        "json": {"()": "apps.common.logging.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_context", "redact"],
            "formatter": "json" if LOG_FORMAT == "json" else "plain",
        }
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "celery": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# API docs exposure (development only by default)
# ---------------------------------------------------------------------------
EXPOSE_API_DOCS = env("EXPOSE_API_DOCS")

# Maximum number of assistants / members for the free plan is defined in
# ``apps.billing.defaults``; entitlement checks always run server side.

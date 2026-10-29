# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/production.txt

# Fail at build time, not at the first request. An image missing a declared
# dependency used to surface as an ImportError inside manage.py, which reads
# like a code bug and is not.
RUN python -c "import django, rest_framework, rest_framework_simplejwt, celery, redis, psycopg, pgvector, cryptography, httpx, openai, pypdf, docx, bs4; print('runtime dependencies ok')"

COPY . .

# The entrypoint must be executable even when the build context comes from a
# filesystem without POSIX permission bits (e.g. Windows).
RUN chmod +x /app/docker/entrypoint.sh \
    && useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "90", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

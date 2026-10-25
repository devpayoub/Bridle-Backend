#!/bin/sh
# Container entrypoint.
#
# Migrations are opt-in (RUN_MIGRATIONS=1) and are meant to run in a dedicated
# one-shot container. Application replicas must never race to migrate.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "[entrypoint] applying database migrations"
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-0}" = "1" ]; then
  echo "[entrypoint] collecting static files"
  python manage.py collectstatic --noinput
fi

exec "$@"

#!/bin/sh
set -e
echo "[start] Running migrations..."
alembic upgrade head
echo "[start] Starting supervisord (web + celery-worker + celery-beat)..."
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf -n

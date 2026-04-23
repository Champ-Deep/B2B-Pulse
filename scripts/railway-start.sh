#!/bin/bash
# Pre-flight validation and startup script for Railway.
#
# Runs BEFORE alembic and gunicorn so that missing env vars produce a clear,
# actionable message instead of a cryptic Pydantic validation traceback.

set -e

# ── Required variables ─────────────────────────────────────────────────────────
MISSING=""

[ -z "$DATABASE_URL" ] && MISSING="$MISSING\n  DATABASE_URL   — PostgreSQL connection string (Railway plugin auto-sets this)"
[ -z "$JWT_SECRET"   ] && MISSING="$MISSING\n  JWT_SECRET     — generate: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
[ -z "$FERNET_KEY"   ] && MISSING="$MISSING\n  FERNET_KEY     — generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""

if [ -n "$MISSING" ]; then
    echo ""
    echo "============================================================"
    echo "  B2B Pulse — STARTUP FAILED: missing environment variables"
    echo "============================================================"
    printf "  Not set:%b\n" "$MISSING"
    echo ""
    echo "  Fix: Railway → this Service → Variables → add the keys above."
    echo "  Full variable reference: docs/SETUP.md"
    echo "============================================================"
    echo ""
    exit 1
fi

# ── Optional-but-important warnings ──────────────────────────────────────────
[ -z "$REDIS_URL" ] && echo "WARN: REDIS_URL not set — Celery tasks will fail. Add the Redis plugin."
[ -z "$LINKEDIN_CLIENT_ID" ] && echo "WARN: LINKEDIN_CLIENT_ID not set — OAuth login will be disabled."
[ -z "$OPENROUTER_API_KEY" ] && echo "WARN: OPENROUTER_API_KEY not set — AI comment generation will fail."

echo ""
echo "Pre-flight OK — required env vars present."

# ── Database migrations ────────────────────────────────────────────────────────
echo "Running: alembic upgrade head"
alembic upgrade head

# ── Server ────────────────────────────────────────────────────────────────────
echo "Starting gunicorn on 0.0.0.0:${PORT:-8000}"
exec gunicorn app.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 4 \
    --access-logfile - \
    --error-logfile -

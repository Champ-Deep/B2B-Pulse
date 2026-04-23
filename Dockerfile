# Root-level Dockerfile for Railway (backend web service).
# Build context is the repo root; app source lives in backend/.
# backend/Dockerfile.prod is the equivalent used by docker-compose.prod.yml
# where the context is already set to ./backend.

# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /app

# Path is relative to repo root (the Railway build context).
COPY backend/pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

COPY --from=builder /install /usr/local

# Install Playwright browsers to a world-readable path.
# Default /root/.cache is inaccessible once we switch to appuser.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" \
    && playwright install chromium --with-deps \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

# Copy everything under backend/ into /app so the layout inside the
# container matches what docker-compose.prod.yml expects:
#   /app/app/        ← Python package
#   /app/alembic/    ← migrations
#   /app/alembic.ini
#   /app/pyproject.toml
COPY --chown=appuser:appuser backend/ .

# Copy the Railway startup script (pre-flight env checks + alembic + gunicorn).
# Installed as root before dropping to appuser so chmod +x works.
COPY scripts/railway-start.sh /usr/local/bin/railway-start
RUN chmod +x /usr/local/bin/railway-start

USER appuser

# Railway injects $PORT at runtime; fall back to 8000 for local builds.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

CMD ["railway-start"]

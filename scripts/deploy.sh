#!/usr/bin/env bash
# =============================================================================
# AutoEngage — Deployment helper script
# Usage: ./scripts/deploy.sh [local|prod-build]
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*"; exit 1; }

# ---- Pre-flight checks ----
check_env() {
    if [ ! -f .env ]; then
        err ".env file not found. Run: cp .env.example .env  — then fill in values."
    fi

    local missing=()
    for var in JWT_SECRET FERNET_KEY DATABASE_URL REDIS_URL; do
        val=$(grep "^${var}=" .env | cut -d= -f2-)
        if [ -z "$val" ] || [[ "$val" == your-* ]]; then
            missing+=("$var")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        err "Missing or placeholder values in .env: ${missing[*]}"
    fi

    log "Environment file looks good"
}

# ---- Generate secrets ----
generate_secrets() {
    log "Generating secrets..."
    echo ""
    echo "JWT_SECRET:"
    python3 -c "import secrets; print(secrets.token_urlsafe(48))"
    echo ""
    echo "FERNET_KEY:"
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    echo ""
    log "Copy these into your .env file or Railway environment variables."
}

# ---- Local development ----
local_dev() {
    check_env
    log "Starting local development stack..."
    docker compose up --build -d
    log "Waiting for services to be healthy..."
    sleep 5
    docker compose ps
    echo ""
    log "Frontend:  http://localhost:5173"
    log "Backend:   http://localhost:8000"
    log "API docs:  http://localhost:8000/docs"
}

# ---- Production build test ----
prod_build() {
    check_env
    log "Building production images..."
    docker build -f backend/Dockerfile.prod -t autoengage-backend:latest ./backend
    docker build -f frontend/Dockerfile.prod -t autoengage-frontend:latest ./frontend
    log "Production images built successfully"
    echo ""
    docker images | grep autoengage
}

# ---- Fix DATABASE_URL for asyncpg ----
fix_database_url() {
    # Railway/Render inject postgresql:// but asyncpg needs postgresql+asyncpg://
    if [ -n "${DATABASE_URL:-}" ]; then
        export DATABASE_URL="${DATABASE_URL/postgresql:\/\//postgresql+asyncpg:\/\/}"
        log "DATABASE_URL driver prefix set to postgresql+asyncpg"
    fi
}

# ---- Main ----
case "${1:-local}" in
    local)
        local_dev
        ;;
    prod-build)
        prod_build
        ;;
    secrets)
        generate_secrets
        ;;
    fix-db-url)
        fix_database_url
        ;;
    *)
        echo "Usage: $0 [local|prod-build|secrets|fix-db-url]"
        echo ""
        echo "  local       — Start local dev stack with docker compose"
        echo "  prod-build  — Build production Docker images"
        echo "  secrets     — Generate JWT_SECRET and FERNET_KEY"
        echo "  fix-db-url  — Transform DATABASE_URL for asyncpg driver"
        exit 1
        ;;
esac

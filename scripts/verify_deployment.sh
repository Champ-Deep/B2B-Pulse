#!/usr/bin/env bash
# =============================================================================
# Post-deploy verification.
#
# Answers one question — "is this deployment actually working?" — with a green
# or red result rather than a wall of output to interpret. Every check prints
# what it expected, so a failure tells you what to fix instead of just that
# something is wrong.
#
# Safe to run as often as you like: everything here is read-only and none of it
# touches LinkedIn.
#
#   ./scripts/verify_deployment.sh                      # local stack
#   ./scripts/verify_deployment.sh https://api.example.com
# =============================================================================

set -uo pipefail

API="${1:-http://localhost:8000}"
COMPOSE="docker compose -f docker-compose.vps.yml"

# Colour only when someone is watching; piping this into a log or a ticket
# should not produce a screenful of escape codes.
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; DIM='\033[2m'; NC='\033[0m'
else
    GREEN=''; RED=''; YELLOW=''; DIM=''; NC=''
fi

PASS=0; FAIL=0; WARN=0

ok()   { echo -e "  ${GREEN}PASS${NC}  $1"; PASS=$((PASS+1)); }
bad()  { echo -e "  ${RED}FAIL${NC}  $1"; [ -n "${2:-}" ] && echo -e "        ${DIM}$2${NC}"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}WARN${NC}  $1"; [ -n "${2:-}" ] && echo -e "        ${DIM}$2${NC}"; WARN=$((WARN+1)); }
head_() { echo; echo -e "── $1 ${DIM}$(printf '─%.0s' $(seq 1 $((60 - ${#1}))))${NC}"; }

# -----------------------------------------------------------------------------
head_ "Configuration"
# -----------------------------------------------------------------------------

if [ ! -f .env ]; then
    bad ".env is missing" "cp .env.vps.example .env and fill it in"
else
    ok ".env exists"

    # Values that must be set, and must not still be the placeholder.
    for var in POSTGRES_PASSWORD JWT_SECRET FERNET_KEY API_DOMAIN CORS_ORIGINS; do
        val=$(grep "^${var}=" .env 2>/dev/null | cut -d= -f2- | tr -d '"')
        if [ -z "$val" ]; then
            bad "$var is empty" "see .env.vps.example for how to generate it"
        elif [[ "$val" == *example.com* ]]; then
            warn "$var still says example.com" "expected your real domain"
        else
            ok "$var is set"
        fi
    done

    # FERNET_KEY must be a valid Fernet key, not just non-empty. A malformed one
    # fails at the moment someone connects an account, which is the worst time
    # to discover it.
    fkey=$(grep "^FERNET_KEY=" .env 2>/dev/null | cut -d= -f2- | tr -d '"')
    if [ -n "$fkey" ]; then
        # A Fernet key is 32 raw bytes, urlsafe-base64 encoded: 44 characters
        # ending in '='. Checked with shell so this works before any Python
        # environment exists — and so "cryptography isn't importable here" can
        # never be misreported as "your key is broken".
        if [ ${#fkey} -eq 44 ] && [[ "$fkey" =~ ^[A-Za-z0-9_-]{43}=$ ]]; then
            ok "FERNET_KEY looks like a valid Fernet key"
        else
            bad "FERNET_KEY is not a valid Fernet key (got ${#fkey} chars, expected 44)" \
                "generate: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        fi
    fi

    # The one setting that silently disables authentication.
    if grep -q "^CLERK_DEV_UNSAFE=true" .env 2>/dev/null; then
        bad "CLERK_DEV_UNSAFE=true" \
            "this accepts unsigned tokens — anyone who can reach the API can mint an admin session"
    else
        ok "CLERK_DEV_UNSAFE is off"
    fi

    # The one setting that silently disables the rate caps.
    if grep -q "^ALLOW_UNCAPPED_SENDING=true" .env 2>/dev/null; then
        bad "ALLOW_UNCAPPED_SENDING=true" \
            "a Redis outage would then remove every per-account cap"
    else
        ok "ALLOW_UNCAPPED_SENDING is off"
    fi

    if git check-ignore -q .env 2>/dev/null; then
        ok ".env is git-ignored"
    else
        bad ".env is NOT git-ignored" "it holds the key to every LinkedIn credential"
    fi
fi

# -----------------------------------------------------------------------------
head_ "Containers"
# -----------------------------------------------------------------------------

if ! docker info >/dev/null 2>&1; then
    warn "Docker is not reachable — skipping container checks"
else
    for svc in postgres redis api worker beat caddy; do
        state=$($COMPOSE ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$svc" '$1==s {print $2}')
        case "$state" in
            running) ok "$svc is running" ;;
            "")      bad "$svc is not up" "docker compose -f docker-compose.vps.yml up -d" ;;
            *)       bad "$svc is $state" "docker compose -f docker-compose.vps.yml logs $svc" ;;
        esac
    done

    # Exactly one beat. Two would make every account plan and execute its day
    # twice — over its own caps, from our own scheduler.
    beats=$($COMPOSE ps --format '{{.Service}}' 2>/dev/null | grep -c '^beat$')
    if [ "$beats" -gt 1 ]; then
        bad "$beats beat containers are running" "there must be exactly one; scale it back to 1"
    fi

    # Migrations are a gated one-shot: if they failed, nothing else should have
    # started, but check explicitly so the reason is visible.
    mig=$($COMPOSE ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null | awk '$1=="migrate"')
    if echo "$mig" | grep -q "exited 0"; then
        ok "migrations completed"
    elif [ -n "$mig" ]; then
        bad "migrations did not succeed ($mig)" "docker compose -f docker-compose.vps.yml logs migrate"
    fi
fi

# -----------------------------------------------------------------------------
head_ "Database"
# -----------------------------------------------------------------------------

if docker info >/dev/null 2>&1 && $COMPOSE ps --format '{{.Service}}' 2>/dev/null | grep -q '^postgres$'; then
    rev=$($COMPOSE exec -T api alembic current 2>/dev/null | grep -oE '^[0-9]+_[a-z_]+' | head -1)
    if [ -n "$rev" ]; then
        ok "schema is at revision $rev"
    else
        warn "could not read the schema revision" "docker compose -f docker-compose.vps.yml exec api alembic current"
    fi

    heads=$($COMPOSE exec -T api alembic heads 2>/dev/null | grep -cE '^[0-9]+_')
    if [ "${heads:-0}" -gt 1 ]; then
        bad "$heads migration heads" "the chain has branched; it must be linear"
    elif [ "${heads:-0}" -eq 1 ]; then
        ok "one migration head"
    fi
fi

# -----------------------------------------------------------------------------
head_ "API"
# -----------------------------------------------------------------------------

health=$(curl -fsS --max-time 15 "$API/health" 2>/dev/null)
if echo "$health" | grep -q '"status":"healthy"'; then
    ok "$API/health responds healthy"
else
    bad "$API/health did not respond healthy" "got: ${health:-<no response>}"
fi

if [[ "$API" == https://* ]]; then
    if curl -fsSI --max-time 15 "$API/health" 2>/dev/null | grep -qi "strict-transport-security"; then
        ok "TLS is terminating and HSTS is set"
    else
        warn "no HSTS header" "check Caddy obtained a certificate: docker compose -f docker-compose.vps.yml logs caddy"
    fi
fi

# An unauthenticated request to a real endpoint must be refused. If this ever
# returns 200, every org's data is public.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$API/api/console/overview" 2>/dev/null)
if [ "$code" = "401" ] || [ "$code" = "403" ]; then
    ok "unauthenticated requests are rejected ($code)"
else
    bad "unauthenticated request returned $code" "expected 401; anything else means the API is open"
fi

# CORS must allow the configured frontend and nothing else.
origin=$(grep "^CORS_ORIGINS=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' | cut -d, -f1)
if [ -n "$origin" ]; then
    allowed=$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS --max-time 15 \
        -H "Origin: $origin" -H "Access-Control-Request-Method: GET" \
        "$API/api/console/overview" 2>/dev/null)
    denied=$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS --max-time 15 \
        -H "Origin: https://not-our-frontend.invalid" -H "Access-Control-Request-Method: GET" \
        "$API/api/console/overview" 2>/dev/null)

    [ "$allowed" = "200" ] \
        && ok "CORS allows $origin" \
        || bad "CORS rejects your own frontend ($origin -> $allowed)" \
               "CORS_ORIGINS must match the browser origin exactly — no trailing slash, right scheme"
    [ "$denied" != "200" ] \
        && ok "CORS rejects other origins" \
        || bad "CORS allows any origin" "CORS_ORIGINS is too permissive"
fi

# -----------------------------------------------------------------------------
head_ "Safety systems"
# -----------------------------------------------------------------------------

if docker info >/dev/null 2>&1 && $COMPOSE ps --format '{{.Service}}' 2>/dev/null | grep -q '^api$'; then
    # The limiter is the control that keeps accounts inside LinkedIn's caps.
    # It runs its decision as a Lua script; if Redis rejects EVAL the caps do
    # not hold, and that must not be discovered on a live account.
    if $COMPOSE exec -T api python -c "
import asyncio, redis.asyncio as aioredis
from app.config import settings
from app.safety.rate_policy import AccountRateLimiter

async def main():
    lim = AccountRateLimiter(aioredis.from_url(settings.redis_url, decode_responses=True))
    # 3 attempts against a cap of 2 -- the third must be refused.
    got = [await lim.check_and_consume('__verify__', '__probe__', 10, 2) for _ in range(3)]
    assert [d.allowed for d in got] == [True, True, False], got
    await lim.redis.delete('rl:__verify__:__probe__', 'rl:__verify__:__probe__:seq')

asyncio.run(main())
" >/dev/null 2>&1; then
        ok "rate limiter enforces caps against live Redis"
    else
        bad "the rate limiter did not enforce a cap" \
            "do not connect an account until this passes: docker compose -f docker-compose.vps.yml exec api python -c 'import redis'"
    fi

    # Redis persistence. Wiping these counters hands every account a fresh
    # daily and weekly allowance — the exact failure the caps exist to prevent.
    aof=$($COMPOSE exec -T redis redis-cli config get appendonly 2>/dev/null | tail -1 | tr -d '\r')
    [ "$aof" = "yes" ] \
        && ok "Redis persistence (AOF) is on" \
        || bad "Redis AOF is off ($aof)" "a restart would reset every account's rate-limit window"

    # The scheduler that drives warm-up. If beat isn't scheduling, accounts
    # look connected and simply never do anything.
    if $COMPOSE exec -T api python -c "
from app.workers.celery_app import celery_app
s = celery_app.conf.beat_schedule
assert 'run-warmup-activity' in s and 'evaluate-warmup-stages' in s, sorted(s)
" >/dev/null 2>&1; then
        ok "warm-up tasks are on the beat schedule"
    else
        bad "warm-up tasks are missing from the beat schedule" "connected accounts would never act"
    fi

    # OpenRouter is optional to boot but not optional in practice: without it
    # generated copy falls back to templates, and template copy is what drives
    # acceptance below the 15% line LinkedIn treats as spam.
    key=$(grep "^OPENROUTER_API_KEY=" .env 2>/dev/null | cut -d= -f2- | tr -d '"')
    [ -n "$key" ] \
        && ok "OpenRouter key is set" \
        || warn "no OPENROUTER_API_KEY" "comments and messages fall back to templates and read like it"
fi

# -----------------------------------------------------------------------------
echo
echo "──────────────────────────────────────────────────────────────"
printf "  ${GREEN}%d passed${NC}   ${RED}%d failed${NC}   ${YELLOW}%d warnings${NC}\n" "$PASS" "$FAIL" "$WARN"
echo "──────────────────────────────────────────────────────────────"

if [ "$FAIL" -gt 0 ]; then
    echo
    echo -e "${RED}Not ready.${NC} Fix the failures above and re-run."
    echo "Do not connect a LinkedIn account while anything is failing."
    exit 1
fi

echo
echo -e "${GREEN}Infrastructure is ready.${NC}"
echo
echo "Next step is NOT yours: connecting a LinkedIn account is the one"
echo "irreversible action here. Hand back to whoever owns the accounts."
echo "See docs/INTERN_DEPLOY_RUNBOOK.md, 'Where you stop'."
exit 0

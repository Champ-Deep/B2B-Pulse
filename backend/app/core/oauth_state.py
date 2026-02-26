"""CSRF-safe OAuth state management using Redis.

Generates cryptographically secure state tokens for OAuth flows,
stored in Redis with a short TTL for single-use validation.
"""

import logging
import secrets

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

OAUTH_STATE_PREFIX = "oauth_state:"
OAUTH_STATE_TTL = 600  # 10 minutes


async def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def create_oauth_state(user_id: str) -> str:
    """Generate a secure random state token and store user_id mapping in Redis."""
    state = secrets.token_urlsafe(32)
    r = await _get_redis()
    try:
        await r.setex(f"{OAUTH_STATE_PREFIX}{state}", OAUTH_STATE_TTL, user_id)
    finally:
        await r.aclose()
    return state


async def validate_oauth_state(state: str) -> str | None:
    """Validate and consume an OAuth state token. Returns user_id or None."""
    r = await _get_redis()
    try:
        key = f"{OAUTH_STATE_PREFIX}{state}"
        user_id = await r.get(key)
        if user_id:
            await r.delete(key)  # Single-use: consume immediately
        return user_id
    finally:
        await r.aclose()


# --- JSON-payload variants for auth flow (stores structured data) ---

AUTH_STATE_PREFIX = "auth_state:"
AUTH_STATE_TTL = 600  # 10 minutes


async def create_auth_oauth_state(payload: dict) -> str:
    """Generate a secure state token and store a JSON payload in Redis."""
    import json

    state = secrets.token_urlsafe(32)
    r = await _get_redis()
    try:
        await r.setex(f"{AUTH_STATE_PREFIX}{state}", AUTH_STATE_TTL, json.dumps(payload))
    finally:
        await r.aclose()
    return state


async def validate_auth_oauth_state(state: str) -> dict | None:
    """Validate and consume an auth state token. Returns the payload dict or None."""
    import json

    r = await _get_redis()
    try:
        key = f"{AUTH_STATE_PREFIX}{state}"
        data = await r.get(key)
        if data:
            await r.delete(key)
            return json.loads(data)
        return None
    finally:
        await r.aclose()

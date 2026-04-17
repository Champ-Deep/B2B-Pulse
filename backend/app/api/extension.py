"""Browser-extension bridge endpoints for LinkedIn session sync.

Flow
----
1. The web app, authenticated as the user, calls `POST /integrations/extension/pair`
   and gets a short-lived pairing token (10-minute TTL, single use on redeem).
2. The web app hands that token to the installed Chrome/Edge extension via
   `chrome.runtime.sendMessage` (the extension declares `externally_connectable`
   for our frontend origin).
3. The extension reads the user's own `li_at` cookie from `.linkedin.com` via
   `chrome.cookies.get` and POSTs it to `/integrations/extension/session-cookies`
   with an `X-Pairing-Token` header.
4. On first successful sync we promote the pairing token to a long-lived
   "extension token" (30-day TTL, rolling) so the extension can keep resyncing
   every few hours without touching the web app.

We deliberately do not reuse the user's JWT in the extension — JWTs are short
and the extension runs for months. A dedicated rolling token keeps the scope
narrow (only cookie-sync endpoints accept it) and revocable.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations import persist_linkedin_session
from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/extension", tags=["extension"])

_PAIRING_PREFIX = "ext_pair:"
_PAIRING_TTL = 600  # 10 minutes, single use

_EXT_TOKEN_PREFIX = "ext_token:"
_EXT_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days, rolling on use


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


class PairingResponse(BaseModel):
    pairing_token: str
    expires_at: str
    api_base: str


class ExtensionCookiesRequest(BaseModel):
    li_at: str = Field(..., min_length=10)
    jsessionid: str | None = Field(default=None, description="Optional JSESSIONID cookie")


class ExtensionCookiesResponse(BaseModel):
    status: str
    user_name: str | None
    session_expires_at: str
    last_session_check: str
    extension_token: str | None = None


@router.post("/pair", summary="Mint a pairing token for the browser extension")
async def mint_pairing_token(current_user: User = Depends(get_current_user)) -> PairingResponse:
    """Return a one-shot pairing token the extension exchanges for an extension token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=_PAIRING_TTL)
    r = await _redis()
    try:
        await r.setex(f"{_PAIRING_PREFIX}{token}", _PAIRING_TTL, str(current_user.id))
    finally:
        await r.aclose()

    # The extension posts cookies back to *this* backend. We prefer an
    # explicit API base from config; otherwise fall back to a localhost
    # default that works for dev. Do NOT use cors_origin_list[0] — that
    # points at the frontend origin.
    api_base = getattr(settings, "api_base_url", "") or "http://localhost:8001"

    return PairingResponse(
        pairing_token=token,
        expires_at=expires_at.isoformat(),
        api_base=api_base,
    )


async def _resolve_extension_caller(pairing_token: str | None) -> tuple[str, bool]:
    """Resolve the incoming X-Pairing-Token to a user_id.

    Accepts either:
      - a fresh pairing token (single use; consumed + upgraded to an extension token)
      - an existing extension token (TTL rolled forward on each use)

    Returns (user_id, is_first_sync). Raises 401 if the token is unknown.
    """
    if not pairing_token:
        raise HTTPException(status_code=401, detail="Missing X-Pairing-Token header")

    r = await _redis()
    try:
        pairing_key = f"{_PAIRING_PREFIX}{pairing_token}"
        user_id = await r.get(pairing_key)
        if user_id:
            await r.delete(pairing_key)
            return user_id, True

        ext_key = f"{_EXT_TOKEN_PREFIX}{pairing_token}"
        user_id = await r.get(ext_key)
        if user_id:
            # Roll the TTL forward so long as the extension stays active.
            await r.expire(ext_key, _EXT_TOKEN_TTL)
            return user_id, False

        raise HTTPException(status_code=401, detail="Pairing token invalid or expired")
    finally:
        await r.aclose()


async def _issue_extension_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    r = await _redis()
    try:
        await r.setex(f"{_EXT_TOKEN_PREFIX}{token}", _EXT_TOKEN_TTL, user_id)
    finally:
        await r.aclose()
    return token


@router.post("/session-cookies", summary="Receive LinkedIn cookies from the browser extension")
async def receive_extension_cookies(
    body: ExtensionCookiesRequest,
    x_pairing_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> ExtensionCookiesResponse:
    """Validate and store cookies posted by the extension.

    Authenticated by the pairing / extension token, NOT by the user's JWT —
    the extension doesn't hold a JWT.
    """
    user_id, is_first_sync = await _resolve_extension_caller(x_pairing_token)

    import uuid as _uuid

    persisted = await persist_linkedin_session(
        db=db,
        user_id=_uuid.UUID(user_id),
        li_at=body.li_at,
        source="extension",
        jsessionid=body.jsessionid,
    )

    extension_token: str | None = None
    if is_first_sync:
        extension_token = await _issue_extension_token(user_id)
        logger.info(f"Extension paired for user {user_id}")

    return ExtensionCookiesResponse(
        status="ok",
        user_name=persisted["user_name"],
        session_expires_at=persisted["session_expires_at"].isoformat(),
        last_session_check=persisted["last_session_check"].isoformat(),
        extension_token=extension_token,
    )


@router.post("/disconnect", summary="Invalidate the extension token for this user")
async def disconnect_extension(
    x_pairing_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Called by the extension when the user clicks 'Disconnect' in the popup."""
    if not x_pairing_token:
        raise HTTPException(status_code=401, detail="Missing X-Pairing-Token header")
    r = await _redis()
    try:
        await r.delete(f"{_EXT_TOKEN_PREFIX}{x_pairing_token}")
    finally:
        await r.aclose()
    return {"status": "disconnected"}

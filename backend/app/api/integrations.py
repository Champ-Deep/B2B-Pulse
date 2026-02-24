import logging
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.oauth_state import create_oauth_state, validate_oauth_state
from app.core.security import encrypt_value
from app.database import get_db
from app.models.integration import IntegrationAccount, Platform
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_SCOPES = "openid profile email w_member_social"

# Meta OAuth constants
META_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
META_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement,instagram_basic,instagram_manage_comments,instagram_content_publish"


@router.get("/linkedin/auth-url")
async def get_linkedin_auth_url(current_user: User = Depends(get_current_user)):
    state = await create_oauth_state(str(current_user.id))
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "scope": LINKEDIN_SCOPES,
        "state": state,
    }
    url = f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": url}


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange LinkedIn OAuth code")

    token_data = response.json()
    user_id = await validate_oauth_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # Calculate token expiry (LinkedIn default: 60 days)
    expires_in = token_data.get("expires_in", 5184000)
    token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Fetch LinkedIn person URN using the new token
    person_settings = {}
    try:
        async with httpx.AsyncClient() as client:
            profile_resp = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
        if profile_resp.status_code == 200:
            profile_data = profile_resp.json()
            person_sub = profile_data.get("sub")
            if person_sub:
                person_settings = {
                    "person_urn": f"urn:li:person:{person_sub}",
                    "name": profile_data.get("name", ""),
                    "email": profile_data.get("email", ""),
                }
                logger.info(f"Captured LinkedIn person URN: urn:li:person:{person_sub}")
    except Exception as e:
        logger.warning(f"Failed to fetch LinkedIn profile: {e}")

    # Check for existing LinkedIn integration
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.access_token = encrypt_value(token_data["access_token"])
        integration.refresh_token = encrypt_value(token_data.get("refresh_token", ""))
        integration.token_expires_at = token_expires_at
        if person_settings:
            integration.settings = person_settings
    else:
        integration = IntegrationAccount(
            user_id=user_id,
            platform=Platform.LINKEDIN,
            access_token=encrypt_value(token_data["access_token"]),
            refresh_token=encrypt_value(token_data.get("refresh_token", "")),
            token_expires_at=token_expires_at,
            settings=person_settings,
        )
        db.add(integration)

    await db.commit()

    # Redirect to frontend
    frontend_url = (
        settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"
    )
    return RedirectResponse(url=f"{frontend_url}/settings?linkedin=connected")


class LinkedInCookiesRequest(BaseModel):
    li_at: str = Field(..., min_length=10, description="LinkedIn li_at cookie value")


@router.post("/linkedin/session-cookies")
async def save_linkedin_session_cookies(
    body: LinkedInCookiesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save LinkedIn browser session cookies for authenticated scraping."""
    cookies = [
        {"name": "li_at", "value": body.li_at, "domain": ".linkedin.com", "path": "/"},
    ]

    # Validate cookies by navigating to LinkedIn
    from app.automation.linkedin_actions import validate_session_cookies

    valid = await validate_session_cookies(cookies)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired LinkedIn cookie. Make sure you copied the full li_at value.",
        )

    # Upsert session cookies on the user's LinkedIn integration
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == current_user.id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.session_cookies = cookies
    else:
        integration = IntegrationAccount(
            user_id=current_user.id,
            platform=Platform.LINKEDIN,
            session_cookies=cookies,
        )
        db.add(integration)

    await db.commit()
    logger.info(f"Saved LinkedIn session cookies for user {current_user.id}")
    return {"status": "valid", "message": "LinkedIn session cookies saved"}


@router.get("/linkedin/session-status")
async def get_linkedin_session_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if the current user has valid LinkedIn session cookies."""
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == current_user.id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()
    return {
        "has_session_cookies": bool(integration and integration.session_cookies),
    }


@router.get("/meta/auth-url")
async def get_meta_auth_url(current_user: User = Depends(get_current_user)):
    state = await create_oauth_state(str(current_user.id))
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "scope": META_SCOPES,
        "response_type": "code",
        "state": state,
    }
    url = f"{META_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": url}


@router.get("/meta/callback")
async def meta_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Step 1: Exchange code for short-lived token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            META_TOKEN_URL,
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
        )

    if response.status_code != 200:
        logger.error(f"Meta token exchange failed: {response.text}")
        raise HTTPException(status_code=400, detail="Failed to exchange Meta OAuth code")

    short_lived = response.json()

    # Step 2: Exchange short-lived token for long-lived token (60 days)
    async with httpx.AsyncClient() as client:
        ll_response = await client.get(
            META_TOKEN_URL,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived["access_token"],
            },
        )

    if ll_response.status_code != 200:
        logger.warning(f"Long-lived token exchange failed, using short-lived: {ll_response.text}")
        token_data = short_lived
    else:
        token_data = ll_response.json()

    user_id = await validate_oauth_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    expires_in = token_data.get("expires_in", 5184000)  # Default 60 days
    token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Upsert Meta integration
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.platform == Platform.META,
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.access_token = encrypt_value(token_data["access_token"])
        integration.token_expires_at = token_expires_at
    else:
        integration = IntegrationAccount(
            user_id=user_id,
            platform=Platform.META,
            access_token=encrypt_value(token_data["access_token"]),
            token_expires_at=token_expires_at,
        )
        db.add(integration)

    await db.commit()

    frontend_url = (
        settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"
    )
    return RedirectResponse(url=f"{frontend_url}/settings?meta=connected")


@router.get("/status")
async def get_integration_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IntegrationAccount).where(IntegrationAccount.user_id == current_user.id)
    )
    integrations = result.scalars().all()

    status_map = {}
    for integration in integrations:
        entry = {
            "connected": True,
            "active": integration.is_active,
        }
        if integration.platform == Platform.LINKEDIN:
            entry["has_session_cookies"] = bool(integration.session_cookies)
        status_map[integration.platform.value] = entry

    return {
        "linkedin": status_map.get(
            "linkedin", {"connected": False, "active": False, "has_session_cookies": False}
        ),
        "meta": status_map.get("meta", {"connected": False, "active": False}),
        "whatsapp": status_map.get("whatsapp", {"connected": False, "active": False}),
    }

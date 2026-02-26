import asyncio
import json
import logging
import urllib.parse
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.linkedin_oauth import (
    LINKEDIN_AUTH_URL,
    LINKEDIN_SCOPES,
    build_person_settings,
    exchange_code_for_token,
    fetch_linkedin_profile,
)
from app.core.oauth_state import create_oauth_state, validate_oauth_state
from app.core.security import encrypt_value
from app.database import get_db
from app.models.integration import IntegrationAccount, Platform
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Meta OAuth constants
META_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
META_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement,instagram_basic,instagram_manage_comments,instagram_content_publish"


@router.get("/linkedin/auth-url", summary="Get LinkedIn OAuth URL")
async def get_linkedin_auth_url(current_user: User = Depends(get_current_user)):
    """Generate the LinkedIn OAuth authorization URL."""
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


@router.get("/linkedin/callback", summary="LinkedIn integration OAuth callback")
async def linkedin_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth redirect for connecting/reconnecting LinkedIn integration."""
    # Exchange code for token using shared utility
    try:
        token_data = await exchange_code_for_token(code, settings.linkedin_redirect_uri)
    except ValueError:
        raise HTTPException(status_code=400, detail="Failed to exchange LinkedIn OAuth code")

    user_id = await validate_oauth_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    expires_in = token_data.get("expires_in", 5184000)
    token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Fetch LinkedIn profile using shared utility
    person_settings = {}
    try:
        profile_data = await fetch_linkedin_profile(token_data["access_token"])
        person_settings = build_person_settings(profile_data)
        if person_settings.get("person_urn"):
            logger.info(f"Captured LinkedIn person URN: {person_settings['person_urn']}")
    except Exception as e:
        logger.warning(f"Failed to fetch LinkedIn profile: {e}")

    # Upsert LinkedIn integration
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

    frontend_url = (
        settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"
    )
    return RedirectResponse(url=f"{frontend_url}/settings?linkedin=connected")


class LinkedInCookiesRequest(BaseModel):
    li_at: str = Field(..., min_length=10, description="LinkedIn li_at cookie value")


@router.post("/linkedin/session-cookies", summary="Save LinkedIn Session Cookies")
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

    validation_result = await validate_session_cookies(cookies)
    if not validation_result.get("valid"):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired LinkedIn cookie. Make sure you copied the full li_at value.",
        )

    # Encrypt cookies before storing
    encrypted_cookies = encrypt_value(json.dumps(cookies))

    # Get user info from validation
    user_name = validation_result.get("user_name")
    user_id = validation_result.get("user_id")

    # Set session expiry (li_at typically lasts ~1 year from last login)
    from datetime import UTC, timedelta

    session_expires = datetime.now(UTC) + timedelta(days=365)

    # Upsert session cookies on the user's LinkedIn integration
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == current_user.id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.session_cookies = encrypted_cookies
        # Use dedicated columns
        if user_name:
            integration.linkedin_user_name = user_name
        if user_id:
            integration.linkedin_user_id = user_id
        integration.session_expires_at = session_expires
        integration.last_session_check = datetime.now(UTC)
    else:
        integration = IntegrationAccount(
            user_id=current_user.id,
            platform=Platform.LINKEDIN,
            session_cookies=encrypted_cookies,
            linkedin_user_name=user_name,
            linkedin_user_id=user_id,
            session_expires_at=session_expires,
            last_session_check=datetime.now(UTC),
        )
        db.add(integration)

    await db.commit()
    logger.info(f"Saved LinkedIn session cookies for user {current_user.id}")

    return {
        "status": "valid",
        "message": "LinkedIn session cookies saved",
        "user_name": user_name,
    }


@router.get("/linkedin/session-status", summary="Get LinkedIn Session Status")
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

    has_cookies = bool(integration and integration.session_cookies)

    # Use dedicated column first, fallback to settings for backwards compatibility
    user_name = None
    if integration:
        user_name = integration.linkedin_user_name
        if not user_name and integration.settings:
            user_name = integration.settings.get("linkedin_user_name")

    session_expires = None
    if integration and integration.session_expires_at:
        session_expires = integration.session_expires_at.isoformat()

    last_check = None
    if integration and integration.last_session_check:
        last_check = integration.last_session_check.isoformat()

    return {
        "has_session_cookies": has_cookies,
        "user_name": user_name,
        "session_expires_at": session_expires,
        "last_session_check": last_check,
    }


@router.get("/meta/auth-url", summary="Get Meta OAuth URL")
async def get_meta_auth_url(current_user: User = Depends(get_current_user)):
    """Generate the Meta (Facebook/Instagram) OAuth authorization URL."""
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


@router.get("/meta/callback", summary="Meta Integration OAuth Callback")
async def meta_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth redirect for connecting/reconnecting Meta integration."""
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


@router.get("/status", summary="Get Integration Status")
async def get_integration_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get connection status for all platform integrations."""
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
            # Use dedicated column first, fallback to settings for backwards compatibility
            user_name = integration.linkedin_user_name
            if not user_name and integration.settings:
                user_name = integration.settings.get("linkedin_user_name")
            entry["user_name"] = user_name
        status_map[integration.platform.value] = entry

    return {
        "linkedin": status_map.get(
            "linkedin", {"connected": False, "active": False, "has_session_cookies": False}
        ),
        "meta": status_map.get("meta", {"connected": False, "active": False}),
        "whatsapp": status_map.get("whatsapp", {"connected": False, "active": False}),
    }


# --- Playwright-assisted LinkedIn Login Flow ---

# Pending verification sessions: session_id -> {pw, browser, context, page, user_id, created_at}
_login_sessions: dict[str, dict] = {}
_LOGIN_SESSION_TTL = 120  # seconds
_MAX_LOGIN_ATTEMPTS_PER_HOUR = 3
_login_attempt_counts: dict[str, list[datetime]] = {}  # user_id -> [timestamps]


async def _cleanup_login_session(session_id: str):
    """Clean up a login session's browser resources."""
    session = _login_sessions.pop(session_id, None)
    if not session:
        return
    import contextlib

    with contextlib.suppress(Exception):
        await session["browser"].close()
    with contextlib.suppress(Exception):
        await session["pw"].stop()


async def _auto_cleanup_session(session_id: str):
    """Auto-cleanup a login session after TTL expires."""
    await asyncio.sleep(_LOGIN_SESSION_TTL)
    await _cleanup_login_session(session_id)


def _check_rate_limit(user_id: str) -> bool:
    """Return True if the user is within the login attempt rate limit."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    attempts = _login_attempt_counts.get(user_id, [])
    attempts = [t for t in attempts if t > cutoff]
    _login_attempt_counts[user_id] = attempts
    return len(attempts) < _MAX_LOGIN_ATTEMPTS_PER_HOUR


class LoginStartRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginVerifyRequest(BaseModel):
    session_id: str
    code: str = Field(..., min_length=1)


@router.post("/linkedin/login-start", summary="Start LinkedIn Login Flow")
async def linkedin_login_start(
    body: LoginStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a Playwright-assisted LinkedIn login flow.

    Credentials are NOT stored — only the resulting session cookies are saved.
    """
    user_id = str(current_user.id)

    if not _check_rate_limit(user_id):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later or use the cookie paste method.",
        )

    _login_attempt_counts.setdefault(user_id, []).append(datetime.now(UTC))

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )

    page = await context.new_page()
    session_id = str(uuid_mod.uuid4())

    try:
        await page.goto(
            "https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000
        )
        await page.fill('input[name="session_key"]', body.email)
        await asyncio.sleep(0.5)
        await page.fill('input[name="session_password"]', body.password)
        await asyncio.sleep(0.3)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(2)  # Wait for redirects to settle

        current_url = page.url

        # Check for verification/challenge
        if "/checkpoint" in current_url or "/challenge" in current_url:
            _login_sessions[session_id] = {
                "pw": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "user_id": user_id,
                "created_at": datetime.now(UTC),
            }
            # Schedule auto-cleanup
            asyncio.create_task(_auto_cleanup_session(session_id))
            return {"status": "needs_verification", "session_id": session_id}

        # Check for successful login
        if (
            "/feed" in current_url
            or "/mynetwork" in current_url
            or current_url.rstrip("/") == "https://www.linkedin.com"
        ):
            cookies = await context.cookies(["https://www.linkedin.com"])
            li_at_list = [c for c in cookies if c["name"] == "li_at"]
            if li_at_list:
                session_cookies = [
                    {
                        "name": "li_at",
                        "value": li_at_list[0]["value"],
                        "domain": ".linkedin.com",
                        "path": "/",
                    }
                ]
                await _save_login_cookies(db, current_user.id, session_cookies)
                return {"status": "success"}
            return {
                "status": "error",
                "error": "Login appeared successful but no session cookie found.",
            }

        # Check for captcha
        captcha = await page.query_selector('[id*="captcha"], [class*="captcha"]')
        if captcha:
            return {"status": "captcha"}

        # Check for wrong credentials
        error_el = await page.query_selector("#error-for-password, .alert-content")
        if error_el:
            return {"status": "error", "error": "Invalid email or password."}

        return {"status": "error", "error": "Login failed. Please try the cookie paste method."}

    except Exception as e:
        logger.warning(f"LinkedIn login-start failed for user {user_id}: {e}")
        return {"status": "error", "error": "Login failed. Please try the cookie paste method."}
    finally:
        # Only clean up if we didn't store the session for verification
        if session_id not in _login_sessions:
            await browser.close()
            await pw.stop()


@router.post("/linkedin/login-verify", summary="Verify LinkedIn Login Code")
async def linkedin_login_verify(
    body: LoginVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Complete LinkedIn login by submitting a verification code."""
    session = _login_sessions.get(body.session_id)
    if not session:
        raise HTTPException(
            status_code=400,
            detail="Login session expired or not found. Please start the login process again.",
        )

    if session["user_id"] != str(current_user.id):
        raise HTTPException(status_code=403, detail="Session does not belong to you.")

    page = session["page"]

    try:
        # Try to find and fill verification code input
        code_input = await page.query_selector(
            'input[name="pin"], input[id*="verification"], input[id*="code"], input[type="tel"]'
        )
        if not code_input:
            await _cleanup_login_session(body.session_id)
            return {
                "status": "error",
                "error": "Could not find verification input. Please use cookie method.",
            }

        await code_input.fill(body.code)
        await asyncio.sleep(0.3)

        # Submit the verification form
        submit_btn = await page.query_selector(
            'button[type="submit"], button[id*="submit"], #two-step-submit-button'
        )
        if submit_btn:
            await submit_btn.click()
        else:
            await page.keyboard.press("Enter")

        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        current_url = page.url

        if (
            "/feed" in current_url
            or "/mynetwork" in current_url
            or current_url.rstrip("/") == "https://www.linkedin.com"
        ):
            cookies = await session["context"].cookies(["https://www.linkedin.com"])
            li_at_list = [c for c in cookies if c["name"] == "li_at"]
            if li_at_list:
                session_cookies = [
                    {
                        "name": "li_at",
                        "value": li_at_list[0]["value"],
                        "domain": ".linkedin.com",
                        "path": "/",
                    }
                ]
                await _save_login_cookies(db, current_user.id, session_cookies)
                return {"status": "success"}

        return {
            "status": "error",
            "error": "Verification may have failed. Try again or use cookie method.",
        }

    except Exception as e:
        logger.warning(f"LinkedIn login-verify failed: {e}")
        return {
            "status": "error",
            "error": "Verification failed. Please use the cookie paste method.",
        }
    finally:
        await _cleanup_login_session(body.session_id)


async def _save_login_cookies(db: AsyncSession, user_id, session_cookies: list[dict]):
    """Save extracted LinkedIn session cookies to the user's integration."""
    # Encrypt cookies before storing
    encrypted_cookies = encrypt_value(json.dumps(session_cookies))

    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()

    if integration:
        integration.session_cookies = encrypted_cookies
    else:
        integration = IntegrationAccount(
            user_id=user_id,
            platform=Platform.LINKEDIN,
            session_cookies=encrypted_cookies,
        )
        db.add(integration)

    await db.commit()
    logger.info(f"Saved LinkedIn session cookies via login flow for user {user_id}")

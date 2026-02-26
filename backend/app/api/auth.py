import logging
import urllib.parse
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.core.oauth_state import create_auth_oauth_state, validate_auth_oauth_state
from app.core.security import create_access_token, create_refresh_token, decode_token, encrypt_value
from app.database import get_db
from app.models.integration import IntegrationAccount, Platform
from app.models.invite import InviteStatus, OrgInvite
from app.models.org import Org
from app.models.tracked_page import PollingMode, TrackedPage, TrackedPageSubscription
from app.models.user import User, UserProfile, UserRole
from app.schemas.auth import RefreshRequest, TokenResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# LinkedIn OAuth login
# ---------------------------------------------------------------------------


@router.get("/linkedin", summary="Initiate LinkedIn OAuth login")
async def auth_linkedin_start(invite_code: str | None = Query(None)):
    """Return a LinkedIn authorization URL. Redirect the user's browser here."""
    payload: dict = {"flow": "auth"}
    if invite_code:
        payload["invite_code"] = invite_code

    state = await create_auth_oauth_state(payload)

    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_auth_redirect_uri,
        "scope": LINKEDIN_SCOPES,
        "state": state,
    }
    url = f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": url}


@router.get("/linkedin/callback", summary="LinkedIn OAuth callback")
async def auth_linkedin_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle the OAuth redirect from LinkedIn, create/login user, and redirect to frontend."""
    frontend_url = settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"

    # Handle LinkedIn-side errors
    if error:
        logger.warning(f"LinkedIn OAuth error: {error} - {error_description}")
        return RedirectResponse(url=f"{frontend_url}/login?error={urllib.parse.quote(error_description or error)}")

    if not code or not state:
        return RedirectResponse(url=f"{frontend_url}/login?error=missing_params")

    # Validate state
    state_payload = await validate_auth_oauth_state(state)
    if not state_payload:
        return RedirectResponse(url=f"{frontend_url}/login?error=invalid_state")

    invite_code = state_payload.get("invite_code")

    # Exchange code for token
    try:
        token_data = await exchange_code_for_token(code, settings.linkedin_auth_redirect_uri)
    except ValueError as e:
        logger.error(f"Token exchange failed: {e}")
        return RedirectResponse(url=f"{frontend_url}/login?error=token_exchange_failed")

    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 5184000)  # Default 60 days
    token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Fetch LinkedIn profile
    try:
        profile = await fetch_linkedin_profile(access_token)
    except ValueError as e:
        logger.error(f"Profile fetch failed: {e}")
        return RedirectResponse(url=f"{frontend_url}/login?error=profile_fetch_failed")

    linkedin_sub = profile.get("sub", "")
    linkedin_email = profile.get("email", "")
    linkedin_name = profile.get("name", "")

    if not linkedin_sub:
        return RedirectResponse(url=f"{frontend_url}/login?error=no_linkedin_id")

    # --- Find existing user ---
    user = None
    is_new_user = False

    # Primary lookup: by linkedin_id
    result = await db.execute(select(User).where(User.linkedin_id == linkedin_sub))
    user = result.scalar_one_or_none()

    # Fallback lookup: by email (migration path for existing password-based users)
    if not user and linkedin_email:
        result = await db.execute(select(User).where(User.email == linkedin_email))
        user = result.scalar_one_or_none()
        if user and not user.linkedin_id:
            # Link LinkedIn to existing account
            user.linkedin_id = linkedin_sub

    if user:
        # --- Existing user: login ---
        if not user.is_active:
            return RedirectResponse(url=f"{frontend_url}/login?error=account_deactivated")

        # Update name if changed on LinkedIn
        if linkedin_name and user.full_name != linkedin_name:
            user.full_name = linkedin_name

    else:
        # --- New user ---
        is_new_user = True
        org = None
        role = UserRole.OWNER
        invite = None
        team_id = None

        if invite_code:
            # Join existing org via invite
            inv_result = await db.execute(
                select(OrgInvite).where(OrgInvite.invite_code == invite_code)
            )
            invite = inv_result.scalar_one_or_none()

            if not invite or invite.status != InviteStatus.PENDING:
                return RedirectResponse(url=f"{frontend_url}/signup?error=invalid_invite")

            if invite.expires_at < datetime.now(UTC):
                invite.status = InviteStatus.EXPIRED
                return RedirectResponse(url=f"{frontend_url}/signup?error=invite_expired")

            if invite.email and invite.email.lower() != linkedin_email.lower():
                return RedirectResponse(url=f"{frontend_url}/signup?error=email_mismatch")

            org_result = await db.execute(select(Org).where(Org.id == invite.org_id))
            org = org_result.scalar_one()
            role = UserRole.MEMBER
            team_id = invite.team_id

            invite.status = InviteStatus.ACCEPTED
            invite.accepted_at = datetime.now(UTC)
        else:
            # Create new org
            org_name = f"{linkedin_name}'s Organization" if linkedin_name else "My Organization"
            org = Org(name=org_name)
            db.add(org)
            await db.flush()

        # Create user
        user = User(
            org_id=org.id,
            email=linkedin_email,
            full_name=linkedin_name or "LinkedIn User",
            linkedin_id=linkedin_sub,
            role=role,
            team_id=team_id,
        )
        db.add(user)
        await db.flush()

        if invite:
            invite.accepted_by = user.id

        # Create empty user profile
        db.add(UserProfile(user_id=user.id))

        # Auto-subscribe invited members to all active tracked pages
        if invite_code and org:
            pages_result = await db.execute(
                select(TrackedPage).where(
                    TrackedPage.org_id == org.id,
                    TrackedPage.active.is_(True),
                )
            )
            for page in pages_result.scalars().all():
                db.add(
                    TrackedPageSubscription(
                        tracked_page_id=page.id,
                        user_id=user.id,
                        auto_like=True,
                        auto_comment=True,
                        polling_mode=PollingMode.NORMAL,
                    )
                )

    # --- Upsert LinkedIn IntegrationAccount ---
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.platform == Platform.LINKEDIN,
        )
    )
    integration = result.scalar_one_or_none()
    person_settings = build_person_settings(profile)

    # Capture session cookies from OAuth token exchange (li_at, JSESSIONID etc.)
    # These are used by Playwright for company page scraping.
    session_cookies = token_data.get("_session_cookies") or None

    if integration:
        integration.access_token = encrypt_value(access_token)
        integration.refresh_token = encrypt_value(token_data.get("refresh_token", ""))
        integration.token_expires_at = token_expires_at
        if person_settings.get("person_urn"):
            integration.settings = person_settings
        if session_cookies:
            integration.session_cookies = session_cookies
    else:
        integration = IntegrationAccount(
            user_id=user.id,
            platform=Platform.LINKEDIN,
            access_token=encrypt_value(access_token),
            refresh_token=encrypt_value(token_data.get("refresh_token", "")),
            token_expires_at=token_expires_at,
            settings=person_settings,
            session_cookies=session_cookies,
        )
        db.add(integration)

    # Issue JWT tokens
    jwt_data = {"sub": str(user.id), "org_id": str(user.org_id)}
    access_jwt = create_access_token(jwt_data)
    refresh_jwt = create_refresh_token(jwt_data)

    # Redirect to frontend with tokens in hash fragment (never hits server logs)
    redirect_path = "/auth/callback"
    fragment = urllib.parse.urlencode({
        "access_token": access_jwt,
        "refresh_token": refresh_jwt,
        "is_new": "1" if is_new_user else "0",
    })
    return RedirectResponse(url=f"{frontend_url}{redirect_path}#{fragment}")


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse, summary="Refresh JWT tokens")
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = decode_token(request.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    token_data = {"sub": str(user.id), "org_id": str(user.org_id)}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse, summary="Get current user")
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user

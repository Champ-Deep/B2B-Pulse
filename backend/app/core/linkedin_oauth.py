"""Shared LinkedIn OAuth utilities used by both auth and integration flows."""

import logging

import httpx

from app.config import HTTP_TIMEOUT, settings

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_SCOPES = "openid profile email w_member_social"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchange an OAuth authorization code for an access token.

    Returns the full token response dict from LinkedIn:
    {access_token, expires_in, scope, token_type, id_token?,
     _session_cookies: list[dict]}  ← list of cookies in Playwright format
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        logger.error(f"LinkedIn token exchange failed: {response.text}")
        raise ValueError(f"LinkedIn token exchange failed (HTTP {response.status_code})")

    data = response.json()

    # Capture any session cookies LinkedIn sets (li_at, JSESSIONID etc.)
    # These can be used for Playwright scraping of company pages that
    # require an authenticated LinkedIn session.
    playwright_cookies = []
    for name, value in response.cookies.items():
        if name in ("li_at", "JSESSIONID", "liap", "li_gc"):
            playwright_cookies.append({
                "name": name,
                "value": value,
                "domain": ".linkedin.com",
                "path": "/",
            })
    if playwright_cookies:
        logger.info(f"Captured {len(playwright_cookies)} LinkedIn session cookies from token exchange")
        data["_session_cookies"] = playwright_cookies

    return data


async def fetch_linkedin_profile(access_token: str) -> dict:
    """Fetch the authenticated user's profile from LinkedIn userinfo endpoint.

    Returns: {sub, email, name, picture, email_verified, ...}
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        logger.error(f"LinkedIn profile fetch failed: {response.text}")
        raise ValueError(f"LinkedIn profile fetch failed (HTTP {response.status_code})")

    return response.json()


def build_person_settings(profile_data: dict) -> dict:
    """Build the settings dict for IntegrationAccount from LinkedIn profile data."""
    person_sub = profile_data.get("sub", "")
    return {
        "person_urn": f"urn:li:person:{person_sub}" if person_sub else "",
        "name": profile_data.get("name", ""),
        "email": profile_data.get("email", ""),
    }

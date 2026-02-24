"""OAuth token refresh logic for LinkedIn and Meta integrations."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TOKEN_REFRESH_BUFFER_DAYS, settings
from app.core.security import decrypt_value, encrypt_value
from app.models.integration import IntegrationAccount, Platform

logger = logging.getLogger(__name__)

LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"

TOKEN_REFRESH_BUFFER = timedelta(days=TOKEN_REFRESH_BUFFER_DAYS)


async def refresh_linkedin_token(
    integration: IntegrationAccount,
    db: AsyncSession,
) -> str:
    """Refresh LinkedIn access token if expired or about to expire.

    Returns the current (or refreshed) decrypted access token.
    """
    if integration.token_expires_at:
        time_until_expiry = integration.token_expires_at - datetime.now(UTC)
        if time_until_expiry > TOKEN_REFRESH_BUFFER:
            return decrypt_value(integration.access_token)

    refresh_token = decrypt_value(integration.refresh_token) if integration.refresh_token else ""
    if not refresh_token:
        logger.warning(f"No refresh token for integration {integration.id}, cannot refresh")
        return decrypt_value(integration.access_token)

    logger.info(f"Refreshing LinkedIn token for integration {integration.id}")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        logger.error(f"LinkedIn token refresh failed: {response.status_code} {response.text}")
        return decrypt_value(integration.access_token)

    token_data = response.json()
    expires_in = token_data.get("expires_in", 5184000)

    integration.access_token = encrypt_value(token_data["access_token"])
    if "refresh_token" in token_data:
        integration.refresh_token = encrypt_value(token_data["refresh_token"])
    integration.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    await db.commit()

    logger.info(f"LinkedIn token refreshed, expires in {expires_in}s")
    return token_data["access_token"]


async def refresh_meta_token(
    integration: IntegrationAccount,
    db: AsyncSession,
) -> str:
    """Refresh Meta long-lived token if expired or about to expire.

    Meta long-lived tokens last 60 days. They can be refreshed to get a new
    60-day token as long as the current one hasn't expired.
    """
    if integration.token_expires_at:
        time_until_expiry = integration.token_expires_at - datetime.now(UTC)
        if time_until_expiry > TOKEN_REFRESH_BUFFER:
            return decrypt_value(integration.access_token)

    current_token = decrypt_value(integration.access_token)
    logger.info(f"Refreshing Meta token for integration {integration.id}")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            META_TOKEN_URL,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": current_token,
            },
        )

    if response.status_code != 200:
        logger.error(f"Meta token refresh failed: {response.status_code} {response.text}")
        return current_token

    token_data = response.json()
    expires_in = token_data.get("expires_in", 5184000)

    integration.access_token = encrypt_value(token_data["access_token"])
    integration.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    await db.commit()

    logger.info(f"Meta token refreshed, expires in {expires_in}s")
    return token_data["access_token"]


async def get_valid_access_token(
    integration: IntegrationAccount,
    db: AsyncSession,
) -> str:
    """Get a valid (refreshed if needed) access token for any platform."""
    if integration.platform == Platform.LINKEDIN:
        return await refresh_linkedin_token(integration, db)
    elif integration.platform == Platform.META:
        return await refresh_meta_token(integration, db)
    return decrypt_value(integration.access_token)

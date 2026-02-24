"""Instagram-specific business logic — Graph API functions for business/creator accounts.

URL utilities have been moved to app.services.url_utils.
"""

import logging

from app.services.meta_client import GRAPH_API_BASE, get_graph_client

logger = logging.getLogger(__name__)


async def get_instagram_business_account(access_token: str, fb_page_id: str) -> str | None:
    """Get the Instagram Business Account ID linked to a Facebook Page."""
    async with get_graph_client() as client:
        resp = await client.get(
            f"{GRAPH_API_BASE}/{fb_page_id}",
            params={
                "fields": "instagram_business_account",
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            logger.error(f"Failed to get IG business account: {resp.text}")
            return None
        data = resp.json()
        ig_account = data.get("instagram_business_account")
        return ig_account["id"] if ig_account else None


async def get_instagram_media(access_token: str, ig_user_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent media from an Instagram Business/Creator account."""
    async with get_graph_client() as client:
        resp = await client.get(
            f"{GRAPH_API_BASE}/{ig_user_id}/media",
            params={
                "fields": "id,caption,media_type,permalink,timestamp,shortcode",
                "limit": limit,
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            logger.error(f"Failed to fetch IG media: {resp.text}")
            return []
        data = resp.json()
        return data.get("data", [])


async def comment_on_instagram_media(access_token: str, media_id: str, message: str) -> dict | None:
    """Comment on an Instagram media item via the Graph API."""
    async with get_graph_client() as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{media_id}/comments",
            data={"message": message, "access_token": access_token},
        )
        if resp.status_code != 200:
            logger.error(f"Failed to comment on IG media {media_id}: {resp.text}")
            return None
        return resp.json()


async def like_instagram_media(access_token: str, media_id: str) -> bool:
    """Like an Instagram media item via the Graph API (business accounts)."""
    # Note: Instagram Graph API does not support liking via API for most accounts.
    # This is a placeholder for accounts with the instagram_manage_comments permission.
    logger.info(f"Graph API like not available for IG media {media_id}, will use Playwright")
    return False

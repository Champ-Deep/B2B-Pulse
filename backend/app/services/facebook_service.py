"""Facebook-specific business logic — Graph API functions for Pages with Page Access Token.

URL utilities have been moved to app.services.url_utils.
"""

import logging

from app.services.meta_client import GRAPH_API_BASE, get_graph_client

logger = logging.getLogger(__name__)


async def get_facebook_page_posts(access_token: str, page_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent posts from a Facebook Page."""
    async with get_graph_client() as client:
        resp = await client.get(
            f"{GRAPH_API_BASE}/{page_id}/posts",
            params={
                "fields": "id,message,created_time,permalink_url,type",
                "limit": limit,
                "access_token": access_token,
            },
        )
        if resp.status_code != 200:
            logger.error(f"Failed to fetch FB page posts: {resp.text}")
            return []
        data = resp.json()
        return data.get("data", [])


async def comment_on_facebook_post(access_token: str, post_id: str, message: str) -> dict | None:
    """Comment on a Facebook post via the Graph API."""
    async with get_graph_client() as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{post_id}/comments",
            data={"message": message, "access_token": access_token},
        )
        if resp.status_code != 200:
            logger.error(f"Failed to comment on FB post {post_id}: {resp.text}")
            return None
        return resp.json()


async def like_facebook_post(access_token: str, post_id: str) -> bool:
    """Like a Facebook post via the Graph API."""
    async with get_graph_client() as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{post_id}/likes",
            data={"access_token": access_token},
        )
        if resp.status_code != 200:
            logger.error(f"Failed to like FB post {post_id}: {resp.text}")
            return False
        return True

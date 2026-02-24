"""LinkedIn REST API client for reactions and comments using OAuth tokens."""

import logging
import re
from urllib.parse import quote

import httpx

from app.config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

LINKEDIN_API_V2 = "https://api.linkedin.com/v2"


def extract_activity_urn_from_url(post_url: str) -> str | None:
    """Extract LinkedIn activity URN from a post URL.

    Handles:
      - linkedin.com/feed/update/urn:li:activity:1234567890
      - linkedin.com/feed/update/urn%3Ali%3Aactivity%3A1234567890  (URL-encoded)
      - linkedin.com/posts/username_title-1234567890-abcd
      - linkedin.com/posts/username_1234567890_something
    """
    # Direct URN in URL (also handle URL-encoded colons)
    decoded = post_url.replace("%3A", ":").replace("%3a", ":")
    urn_match = re.search(r"urn:li:activity:(\d+)", decoded)
    if urn_match:
        return f"urn:li:activity:{urn_match.group(1)}"

    # /posts/ pattern — activity ID is a 19-20 digit number
    # Format: /posts/username_slug-1234567890123456789-xxxx
    posts_match = re.search(r"/posts/[^/]+-(\d{19,20})-", post_url)
    if posts_match:
        return f"urn:li:activity:{posts_match.group(1)}"

    # Alternative /posts/ format: /posts/username_1234567890123456789_something
    posts_match2 = re.search(r"/posts/[^_]+_(\d{19,20})_", post_url)
    if posts_match2:
        return f"urn:li:activity:{posts_match2.group(1)}"

    # /feed/update/ without urn prefix but with activity ID
    feed_match = re.search(r"/feed/update/activity:(\d+)", decoded)
    if feed_match:
        return f"urn:li:activity:{feed_match.group(1)}"

    logger.warning(f"Could not extract activity URN from URL: {post_url}")
    return None


def _extract_thread_urn(error_text: str) -> str | None:
    """Extract the actual thread URN from a LinkedIn API error message."""
    match = re.search(r"actual threadUrn: (urn:li:activity:\d+)", error_text)
    return match.group(1) if match else None


async def _social_action_request(
    client: httpx.AsyncClient,
    access_token: str,
    person_urn: str,
    activity_urn: str,
    action: str,
    body: dict,
) -> httpx.Response:
    """Make a v2 socialActions request, handling thread URN mismatch for reshares."""
    encoded_urn = quote(activity_urn, safe="")
    resp = await client.post(
        f"{LINKEDIN_API_V2}/socialActions/{encoded_urn}/{action}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=body,
    )

    # Handle thread URN mismatch (reshared posts) — retry on the thread root
    if resp.status_code == 400:
        thread_urn = _extract_thread_urn(resp.text)
        if thread_urn:
            logger.info(f"Reshare detected, retrying {action} on thread root: {thread_urn}")
            encoded_thread = quote(thread_urn, safe="")
            resp = await client.post(
                f"{LINKEDIN_API_V2}/socialActions/{encoded_thread}/{action}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

    return resp


async def react_to_post(
    access_token: str,
    person_urn: str,
    activity_urn: str,
    reaction_type: str = "LIKE",
) -> bool:
    """React (like) a LinkedIn post via v2 socialActions API.

    Valid reaction types: LIKE, PRAISE, EMPATHY, INTEREST, APPRECIATION
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await _social_action_request(
            client,
            access_token,
            person_urn,
            activity_urn,
            action="likes",
            body={"actor": person_urn},
        )

        if resp.status_code in (200, 201):
            logger.info(f"Reacted to {activity_urn} as {person_urn}")
            return True
        elif resp.status_code == 409:
            logger.info(f"Already reacted to {activity_urn}")
            return True
        else:
            logger.error(f"LinkedIn reaction failed ({resp.status_code}): {resp.text}")
            return False


async def comment_on_post(
    access_token: str,
    person_urn: str,
    activity_urn: str,
    comment_text: str,
) -> bool:
    """Comment on a LinkedIn post via v2 socialActions API."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await _social_action_request(
            client,
            access_token,
            person_urn,
            activity_urn,
            action="comments",
            body={
                "actor": person_urn,
                "message": {"text": comment_text},
            },
        )

        if resp.status_code in (200, 201):
            logger.info(f"Commented on {activity_urn}")
            return True
        else:
            logger.error(f"LinkedIn comment failed ({resp.status_code}): {resp.text}")
            return False


async def get_person_urn(access_token: str) -> str | None:
    """Fetch the authenticated user's person URN."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(
            f"{LINKEDIN_API_V2}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 200:
            data = resp.json()
            sub = data.get("sub")
            if sub:
                return f"urn:li:person:{sub}"
    return None

"""LinkedIn REST API helpers for fetching posts from tracked pages.

Uses the OAuth access tokens stored by the auth flow (via IntegrationAccount.access_token)
instead of Playwright browser scraping.  This is faster, more reliable, within ToS,
and avoids the SoftTimeLimitExceeded issue that plagued Playwright polling.

Relevant LinkedIn API docs:
  - UGC Posts: https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/ugc-post-api
  - Organization lookup: https://api.linkedin.com/v2/organizations?q=vanityName&vanityName=<slug>
"""

import logging
import re

import httpx

from app.config import HTTP_TIMEOUT

logger = logging.getLogger(__name__)

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


# ---------------------------------------------------------------------------
# Organization / person URN resolution
# ---------------------------------------------------------------------------


async def resolve_company_urn(access_token: str, vanity_name: str) -> str | None:
    """Look up a company's LinkedIn URN by its URL slug (vanity name).

    E.g. 'lakeb2b' from 'https://www.linkedin.com/company/lakeb2b/'
    Returns e.g. 'urn:li:organization:12345678' or None if not found.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    url = f"{LINKEDIN_API_BASE}/organizations"
    params = {"q": "vanityName", "vanityName": vanity_name}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            elements = data.get("elements", [])
            if elements:
                org_id = elements[0].get("id")
                if org_id:
                    return f"urn:li:organization:{org_id}"
        else:
            logger.warning(
                f"LinkedIn organization lookup failed for '{vanity_name}': "
                f"HTTP {resp.status_code} — {resp.text[:300]}"
            )
    except Exception as e:
        logger.warning(f"LinkedIn organization URN lookup error for '{vanity_name}': {e}")
    return None


def extract_vanity_name(url: str) -> str | None:
    """Extract the company slug from a LinkedIn company URL.

    E.g. 'https://www.linkedin.com/company/lakeb2b/' → 'lakeb2b'
    """
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url)
    return m.group(1).rstrip("/") if m else None


def extract_person_vanity(url: str) -> str | None:
    """Extract the person vanity name from a LinkedIn profile URL.

    E.g. 'https://www.linkedin.com/in/sreedeep/' → 'sreedeep'
    """
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
    return m.group(1).rstrip("/") if m else None


# ---------------------------------------------------------------------------
# Post fetching
# ---------------------------------------------------------------------------


async def fetch_company_posts(
    access_token: str,
    author_urn: str,
    limit: int = 10,
) -> list[dict]:
    """Fetch recent UGC posts authored by a company or person URN.

    Returns a list of dicts with keys: external_id, url, content.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202401",
    }

    # Try UGC Posts endpoint first (supports both org and person authors)
    posts = await _fetch_ugc_posts(headers, author_urn, limit)
    if posts is not None:
        return posts

    # Fallback: shares endpoint (older API)
    posts = await _fetch_shares(headers, author_urn, limit)
    if posts is not None:
        return posts

    logger.warning(f"All LinkedIn post fetch methods exhausted for author {author_urn}")
    return []


async def _fetch_ugc_posts(headers: dict, author_urn: str, limit: int) -> list[dict] | None:
    """Fetch via /v2/ugcPosts endpoint."""
    url = f"{LINKEDIN_API_BASE}/ugcPosts"
    params = {
        "q": "authors",
        "authors": f"List({author_urn})",
        "count": min(limit, 10),
        "sortBy": "LAST_MODIFIED",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            return _parse_ugc_posts(data.get("elements", []))
        elif resp.status_code in (400, 403, 404):
            logger.debug(
                f"UGC posts not available for {author_urn}: {resp.status_code} — {resp.text[:200]}"
            )
            return None  # Signal to try fallback
        else:
            logger.warning(
                f"LinkedIn UGC posts fetch HTTP {resp.status_code} for {author_urn}: {resp.text[:300]}"
            )
            return None
    except Exception as e:
        logger.warning(f"LinkedIn UGC posts fetch error for {author_urn}: {e}")
        return None


async def _fetch_shares(headers: dict, author_urn: str, limit: int) -> list[dict] | None:
    """Fetch via /v2/shares endpoint (older fallback)."""
    url = f"{LINKEDIN_API_BASE}/shares"
    params = {
        "q": "owners",
        "owners": author_urn,
        "count": min(limit, 10),
        "sortBy": "LAST_MODIFIED",
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            return _parse_shares(data.get("elements", []))
        else:
            logger.warning(
                f"LinkedIn shares fetch HTTP {resp.status_code} for {author_urn}: {resp.text[:300]}"
            )
            return None
    except Exception as e:
        logger.warning(f"LinkedIn shares fetch error for {author_urn}: {e}")
        return None


def _parse_ugc_posts(elements: list) -> list[dict]:
    """Parse LinkedIn UGC post elements into our standard format."""
    results = []
    for el in elements:
        post_id = el.get("id", "")
        # Build the public post URL from the URN
        # UGC post IDs look like 'urn:li:ugcPost:7210000000000000000'
        urn_id = post_id.split(":")[-1] if ":" in post_id else post_id
        post_url = f"https://www.linkedin.com/feed/update/{post_id}/"

        # Extract text content
        content = ""
        try:
            content = (
                el.get("specificContent", {})
                .get("com.linkedin.ugc.ShareContent", {})
                .get("shareCommentary", {})
                .get("text", "")
            )
        except (AttributeError, KeyError):
            pass

        if post_id:
            results.append({
                "external_id": post_id,
                "url": post_url,
                "content": content[:2000],
            })
    return results


def _parse_shares(elements: list) -> list[dict]:
    """Parse LinkedIn share elements into our standard format."""
    results = []
    for el in elements:
        share_id = el.get("id", "")
        post_url = f"https://www.linkedin.com/feed/update/urn:li:share:{share_id}/"

        content = ""
        try:
            content = (
                el.get("text", {}).get("text", "")
                or el.get("specificContent", {})
                .get("com.linkedin.ugc.ShareContent", {})
                .get("shareCommentary", {})
                .get("text", "")
            )
        except (AttributeError, KeyError):
            pass

        if share_id:
            results.append({
                "external_id": f"urn:li:share:{share_id}",
                "url": post_url,
                "content": content[:2000],
            })
    return results

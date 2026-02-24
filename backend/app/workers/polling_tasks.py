"""Polling task worker — discover new posts on tracked social-media pages.

Architecture notes:
- Uses asyncio.run() per invocation → requires get_task_session() for DB
  access (see engagement_tasks.py docstring for full rationale).
- Imports are deferred inside async helpers to avoid circular imports and
  keep Celery's module graph lean.
- Beat schedule fires dispatch_poll_tasks which fans out individual
  poll_single_page_task calls — one per tracked page — so Celery workers
  can poll pages in parallel instead of sequentially.
- Per-page Redis locks prevent duplicate polls when beat fires faster
  than pages can be scraped.
"""

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

POLL_PAGE_LOCK_PREFIX = "autoengage:poll_page:"
POLL_PAGE_LOCK_TTL = 110  # seconds — less than soft_time_limit of 120


@celery_app.task(
    name="app.workers.polling_tasks.dispatch_poll_tasks",
    soft_time_limit=30,
    time_limit=60,
)
def dispatch_poll_tasks():
    """Beat task: fan out individual poll tasks for each active tracked page."""
    asyncio.run(_dispatch_polls())


async def _dispatch_polls():
    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.tracked_page import TrackedPage

    async with get_task_session() as db:
        result = await db.execute(select(TrackedPage.id).where(TrackedPage.active.is_(True)))
        page_ids = [str(row[0]) for row in result.all()]

    for page_id in page_ids:
        poll_single_page_task.delay(page_id)

    logger.info(f"Dispatched {len(page_ids)} individual poll tasks")


@celery_app.task(
    name="app.workers.polling_tasks.poll_single_page_task",
    soft_time_limit=120,
    time_limit=150,
    max_retries=1,
    default_retry_delay=30,
)
def poll_single_page_task(tracked_page_id: str):
    """Poll a single tracked page for new posts."""
    import redis as sync_redis

    from app.config import settings

    # Per-page deduplication lock
    r = sync_redis.from_url(settings.redis_url)
    lock_key = f"{POLL_PAGE_LOCK_PREFIX}{tracked_page_id}"
    acquired = r.set(lock_key, "1", nx=True, ex=POLL_PAGE_LOCK_TTL)
    if not acquired:
        logger.debug(f"Page {tracked_page_id} already being polled, skipping")
        return

    try:
        asyncio.run(_poll_page_by_id(tracked_page_id))
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            r.delete(lock_key)


async def _poll_page_by_id(tracked_page_id: str):
    import json
    import uuid
    from datetime import UTC, datetime

    import redis as sync_redis
    from sqlalchemy import select

    from app.config import settings
    from app.database import get_task_session
    from app.models.tracked_page import TrackedPage

    status_key = f"autoengage:poll_status:{tracked_page_id}"
    r = sync_redis.from_url(settings.redis_url)

    async with get_task_session() as db:
        result = await db.execute(
            select(TrackedPage).where(TrackedPage.id == uuid.UUID(tracked_page_id))
        )
        page = result.scalar_one_or_none()
        if not page:
            logger.warning(f"Tracked page {tracked_page_id} not found")
            return
        if not page.active:
            logger.debug(f"Tracked page {tracked_page_id} is inactive, skipping")
            return

        try:
            poll_result = await _poll_single_page(db, page)
            r.set(
                status_key,
                json.dumps({
                    "last_polled_at": datetime.now(UTC).isoformat(),
                    "status": poll_result.get("status", "ok"),
                    "posts_found": poll_result.get("posts_found", 0),
                    "new_posts": poll_result.get("new_posts", 0),
                    "error": poll_result.get("error"),
                }),
                ex=3600,
            )
        except Exception as e:
            logger.warning(f"Error polling page {page.id} ({page.url}): {e}")
            r.set(
                status_key,
                json.dumps({
                    "last_polled_at": datetime.now(UTC).isoformat(),
                    "status": "error",
                    "posts_found": 0,
                    "new_posts": 0,
                    "error": str(e),
                }),
                ex=3600,
            )

        await db.commit()


async def _poll_single_page(db, page) -> dict:
    """Poll a single tracked page for new posts using Playwright or Graph API.

    Returns a status dict with keys: status, posts_found, new_posts, error.
    """
    from sqlalchemy import select

    from app.models.integration import Platform
    from app.models.post import Post
    from app.models.tracked_page import PageType

    logger.info(f"Polling page: {page.name} ({page.url})")

    posts_data = []

    if page.platform == Platform.LINKEDIN:
        from app.automation.linkedin_actions import scrape_profile_posts

        li_cookies = await _get_linkedin_cookies(db, page.org_id)
        if li_cookies is None:
            logger.warning(
                f"Skipping LinkedIn poll for {page.url}: no session cookies configured. "
                "A user must paste their li_at cookie in Settings."
            )
            return {"status": "no_cookies", "posts_found": 0, "new_posts": 0, "error": None}
        try:
            posts_data = await scrape_profile_posts(page.url, cookies=li_cookies)
        except Exception as e:
            logger.warning(f"Playwright scrape failed for {page.url}: {e}")
            return {"status": "error", "posts_found": 0, "new_posts": 0, "error": str(e)}

    elif page.platform == Platform.META:
        # Try Graph API first for business accounts, fall back to Playwright
        if page.page_type in (PageType.IG_BUSINESS, PageType.FB_PAGE):
            posts_data = await _poll_meta_api(db, page)
        else:
            posts_data = await _poll_meta_playwright(page)
    else:
        logger.warning(f"Unsupported platform for polling: {page.platform}")
        return {"status": "error", "posts_found": 0, "new_posts": 0, "error": "Unsupported platform"}

    from sqlalchemy.exc import IntegrityError

    new_count = 0
    for post_data in posts_data:
        # Check if we already know about this post
        result = await db.execute(
            select(Post).where(Post.external_post_id == post_data["external_id"])
        )
        if result.scalar_one_or_none():
            continue

        # New post found — use IntegrityError catch for race condition safety
        post = Post(
            tracked_page_id=page.id,
            platform=page.platform,
            external_post_id=post_data["external_id"],
            url=post_data["url"],
            content_text=post_data.get("content"),
        )
        db.add(post)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.debug(f"Post {post_data['external_id']} already exists (concurrent insert)")
            continue

        new_count += 1
        logger.info(f"New post detected: {post.url}")

        # Enqueue engagement jobs
        from app.workers.engagement_tasks import schedule_staggered_engagements

        schedule_staggered_engagements.delay(str(post.id), str(page.id))

    return {"status": "ok", "posts_found": len(posts_data), "new_posts": new_count, "error": None}


async def _get_linkedin_cookies(db, org_id) -> list[dict] | None:
    """Find LinkedIn session cookies from any org member's integration."""
    from sqlalchemy import select

    from app.models.integration import IntegrationAccount, Platform
    from app.models.user import User

    result = await db.execute(
        select(IntegrationAccount)
        .join(User, User.id == IntegrationAccount.user_id)
        .where(
            User.org_id == org_id,
            IntegrationAccount.platform == Platform.LINKEDIN,
            IntegrationAccount.is_active.is_(True),
            IntegrationAccount.session_cookies.isnot(None),
        )
        .limit(1)
    )
    integration = result.scalar_one_or_none()
    if integration and integration.session_cookies:
        cookies = integration.session_cookies
        # Ensure cookies are in Playwright format (list of dicts with name, value, domain)
        if isinstance(cookies, list):
            return cookies
        elif isinstance(cookies, dict):
            return [
                {"name": k, "value": v, "domain": ".linkedin.com", "path": "/"}
                for k, v in cookies.items()
            ]
    return None


async def _poll_meta_api(db, page):
    """Poll a Meta page/account via Graph API."""
    from sqlalchemy import select

    from app.core.security import decrypt_value
    from app.models.integration import IntegrationAccount, Platform
    from app.models.tracked_page import PageType

    # Find any Meta integration that has access (use the org's first connected Meta account)
    result = await db.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.platform == Platform.META,
            IntegrationAccount.is_active.is_(True),
        )
    )
    integration = result.scalars().first()
    if not integration:
        logger.warning(f"No active Meta integration found for polling page {page.id}")
        return []

    access_token = decrypt_value(integration.access_token)

    if page.page_type == PageType.IG_BUSINESS:
        from app.services.instagram_service import get_instagram_media

        if not page.external_id:
            return []
        media = await get_instagram_media(access_token, page.external_id, limit=10)
        return [
            {
                "external_id": f"ig_{item.get('shortcode', item['id'])}",
                "url": item.get(
                    "permalink", f"https://www.instagram.com/p/{item.get('shortcode', '')}"
                ),
                "content": item.get("caption", ""),
            }
            for item in media
        ]
    elif page.page_type == PageType.FB_PAGE:
        from app.services.facebook_service import get_facebook_page_posts

        if not page.external_id:
            return []
        fb_posts = await get_facebook_page_posts(access_token, page.external_id, limit=10)
        return [
            {
                "external_id": f"fb_{item['id']}",
                "url": item.get("permalink_url", f"https://www.facebook.com/{item['id']}"),
                "content": item.get("message", ""),
            }
            for item in fb_posts
        ]
    return []


async def _poll_meta_playwright(page):
    """Poll a Meta page using Playwright (for personal accounts)."""
    from app.services.url_utils import is_facebook_url, is_instagram_url

    if is_instagram_url(page.url):
        from app.automation.instagram_actions import scrape_profile_posts

        try:
            return await scrape_profile_posts(page.url)
        except Exception as e:
            logger.warning(f"IG Playwright scrape failed for {page.url}: {e}")
            return []
    elif is_facebook_url(page.url):
        from app.automation.facebook_actions import scrape_page_posts

        try:
            return await scrape_page_posts(page.url)
        except Exception as e:
            logger.warning(f"FB Playwright scrape failed for {page.url}: {e}")
            return []
    return []

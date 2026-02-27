"""Polling task worker — discover new posts on tracked social-media pages.

Architecture notes:
- Uses asyncio.run() per invocation → requires get_task_session() for DB
  access (see engagement_tasks.py docstring for full rationale).
- Beat schedule fires dispatch_poll_tasks which fans out individual
  poll_single_page_task calls — one per tracked page — so Celery workers
  can poll pages in parallel.
- Per-page Redis locks prevent duplicate polls when beat fires faster
  than pages can be scraped.
- LinkedIn polling uses the OAuth REST API (not Playwright) to fetch posts
  using the stored access_token from IntegrationAccount.
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
    import json

    import redis as sync_redis
    from sqlalchemy import select

    from app.config import settings
    from app.database import get_task_session
    from app.models.tracked_page import TrackedPage
    from app.models.user import User, UserProfile

    r = sync_redis.from_url(settings.redis_url)

    async with get_task_session() as db:
        result = await db.execute(
            select(TrackedPage.id, TrackedPage.org_id).where(TrackedPage.active.is_(True))
        )
        pages = result.all()

        # Cache org polling intervals to avoid repeated queries
        org_intervals: dict[str, int] = {}

        dispatched = 0
        for page_id_val, org_id_val in pages:
            page_id = str(page_id_val)
            org_id = str(org_id_val)

            # Get org's polling interval (cached per org)
            if org_id not in org_intervals:
                user_result = await db.execute(
                    select(UserProfile)
                    .join(User, User.id == UserProfile.user_id)
                    .where(User.org_id == org_id_val, User.is_active.is_(True))
                    .limit(1)
                )
                up = user_result.scalar_one_or_none()
                interval = 300  # default 5 min
                if up and up.automation_settings:
                    interval = up.automation_settings.get("polling_interval", 300)
                org_intervals[org_id] = interval

            poll_interval = org_intervals[org_id]

            # Check last poll time from Redis — skip if polled too recently
            status_raw = r.get(f"autoengage:poll_status:{page_id}")
            if status_raw:
                try:
                    from datetime import UTC, datetime

                    status_data = json.loads(status_raw)
                    last_polled = status_data.get("last_polled_at")
                    if last_polled:
                        last_dt = datetime.fromisoformat(last_polled)
                        elapsed = (datetime.now(UTC) - last_dt).total_seconds()
                        if elapsed < poll_interval - 30:  # 30s grace for scheduling jitter
                            continue
                except (json.JSONDecodeError, ValueError):
                    pass

            poll_single_page_task.delay(page_id)
            dispatched += 1

    logger.info(f"Dispatched {dispatched}/{len(pages)} poll tasks (respecting org intervals)")


@celery_app.task(
    name="app.workers.polling_tasks.poll_single_page_task",
    soft_time_limit=60,  # Reduced from 120s — API calls are fast, no Playwright
    time_limit=90,
    max_retries=2,
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

        poll_result: dict
        try:
            poll_result = await _poll_single_page(db, page)
        except Exception as e:
            logger.warning(f"Error polling page {page.id} ({page.url}): {e}")
            poll_result = {
                "status": "error",
                "posts_found": 0,
                "new_posts": 0,
                "error": str(e),
            }

        now_iso = datetime.now(UTC).isoformat()
        status_payload = {
            "last_polled_at": now_iso,
            "status": poll_result.get("status", "ok"),
            "posts_found": poll_result.get("posts_found", 0),
            "new_posts": poll_result.get("new_posts", 0),
            "error": poll_result.get("error"),
        }

        # Write to Redis (short-term, for fast UI reads)
        r.set(status_key, json.dumps(status_payload), ex=86400)  # 24hr TTL

        # Write to DB (persistent — survives Redis flush/TTL)
        page.last_polled_at = datetime.now(UTC)
        page.last_poll_status = poll_result.get("status", "ok")[:50]

        await db.commit()


async def _poll_single_page(db, page) -> dict:
    """Poll a single tracked page for new posts.

    LinkedIn: uses OAuth REST API (access_token from IntegrationAccount).
    Meta: uses Graph API for business pages, Playwright for personal.

    Returns a status dict: {status, posts_found, new_posts, error}.
    """
    from sqlalchemy import select

    from app.models.integration import Platform
    from app.models.post import Post
    from app.models.tracked_page import PageType

    logger.info(f"Polling page: {page.name} ({page.url})")

    posts_data: list[dict] = []

    if page.platform == Platform.LINKEDIN:
        posts_data = await _poll_linkedin_api(db, page)

    elif page.platform == Platform.META:
        if page.page_type in (PageType.IG_BUSINESS, PageType.FB_PAGE):
            posts_data = await _poll_meta_api(db, page)
        else:
            posts_data = await _poll_meta_playwright(page)
    else:
        logger.warning(f"Unsupported platform for polling: {page.platform}")
        return {
            "status": "error",
            "posts_found": 0,
            "new_posts": 0,
            "error": "Unsupported platform",
        }

    from sqlalchemy.exc import IntegrityError

    new_count = 0
    for post_data in posts_data:
        result = await db.execute(
            select(Post).where(Post.external_post_id == post_data["external_id"])
        )
        if result.scalar_one_or_none():
            continue

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

        from app.workers.engagement_tasks import schedule_staggered_engagements

        schedule_staggered_engagements.delay(str(post.id), str(page.id))

    return {"status": "ok", "posts_found": len(posts_data), "new_posts": new_count, "error": None}


# ---------------------------------------------------------------------------
# LinkedIn API polling (replaces Playwright scraping)
# ---------------------------------------------------------------------------


async def _get_linkedin_access_token(db, org_id) -> str | None:
    """Return a decrypted LinkedIn OAuth access token for any active org member.

    Prefers tokens that haven't expired yet.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.core.security import decrypt_value
    from app.models.integration import IntegrationAccount, Platform
    from app.models.user import User

    result = await db.execute(
        select(IntegrationAccount)
        .join(User, User.id == IntegrationAccount.user_id)
        .where(
            User.org_id == org_id,
            IntegrationAccount.platform == Platform.LINKEDIN,
            IntegrationAccount.is_active.is_(True),
            IntegrationAccount.access_token.isnot(None),
        )
        .order_by(IntegrationAccount.token_expires_at.desc())  # Prefer freshest token
        .limit(1)
    )
    integration = result.scalar_one_or_none()
    if not integration:
        return None

    # Warn if token may be expired
    if integration.token_expires_at and integration.token_expires_at < datetime.now(UTC):
        logger.warning(
            f"LinkedIn token for org {org_id} may be expired "
            f"(expires_at={integration.token_expires_at}). Attempting anyway."
        )

    try:
        return decrypt_value(integration.access_token)
    except Exception as e:
        logger.error(f"Failed to decrypt LinkedIn access token for org {org_id}: {e}")
        return None


async def _poll_linkedin_api(db, page) -> list[dict]:
    """Fetch recent LinkedIn posts using stored session cookies via Playwright.

    Falls back gracefully if no cookies are present (directing users to sign in again).
    """
    from app.models.tracked_page import PageType

    cookies = await _get_linkedin_cookies(db, page.org_id)
    if not cookies:
        logger.warning(
            f"No LinkedIn session cookies for org {page.org_id}. "
            "User must re-authenticate via Sign in with LinkedIn to refresh cookies."
        )
        return []

    logger.info(f"Polling LinkedIn page via Playwright: {page.name} ({page.url})")
    from app.automation.linkedin_actions import scrape_profile_posts

    try:
        return await scrape_profile_posts(page.url, cookies=cookies)
    except Exception as e:
        logger.warning(f"Playwright scrape failed for {page.url}: {e}")
        return []


async def _get_linkedin_cookies(db, org_id) -> list[dict] | None:
    """Return LinkedIn session cookies for any active org member.
    Handles both encrypted (string) and plain (list/dict) cookie formats.
    Returns cookies in Playwright format: list of {name, value, domain, path}.
    """
    import json

    from sqlalchemy import select

    from app.core.security import decrypt_value
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
    if not integration or not integration.session_cookies:
        return None

    cookies_data = integration.session_cookies

    # Handle encrypted cookies (stored as string after encryption changes)
    if isinstance(cookies_data, str):
        try:
            decrypted = decrypt_value(cookies_data)
            cookies_data = json.loads(decrypted)
        except Exception:
            logger.warning(f"Failed to decrypt cookies for org {org_id}")
            return None

    # Normalise to Playwright format
    if isinstance(cookies_data, list):
        return cookies_data
    elif isinstance(cookies_data, dict):
        return [
            {"name": k, "value": v, "domain": ".linkedin.com", "path": "/"}
            for k, v in cookies_data.items()
        ]
    return None


# ---------------------------------------------------------------------------
# Meta API polling (unchanged)
# ---------------------------------------------------------------------------


async def _poll_meta_api(db, page):
    """Poll a Meta page/account via Graph API."""
    from sqlalchemy import select

    from app.core.security import decrypt_value
    from app.models.integration import IntegrationAccount, Platform
    from app.models.tracked_page import PageType

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

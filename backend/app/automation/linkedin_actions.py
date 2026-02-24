import asyncio
import logging
import uuid

from playwright.async_api import Page

from app.automation.browser_manager import get_page, human_delay

logger = logging.getLogger(__name__)


async def _get_user_cookies(user_id: str) -> list[dict] | None:
    """Load stored session cookies for a user's LinkedIn integration."""
    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.integration import IntegrationAccount, Platform

    async with get_task_session() as db:
        result = await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == uuid.UUID(user_id),
                IntegrationAccount.platform == Platform.LINKEDIN,
            )
        )
        integration = result.scalar_one_or_none()
        if integration and integration.session_cookies:
            return integration.session_cookies
    return None


async def _get_page_for_user(user_id: str) -> Page:
    """Get a Playwright page with the user's LinkedIn session."""
    cookies = await _get_user_cookies(user_id)
    page = await get_page(user_id, cookies)
    return page


async def check_session_valid(user_id: str) -> bool:
    """Check if the user's LinkedIn session is still valid."""
    page = await _get_page_for_user(user_id)
    try:
        await page.goto(
            "https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000
        )
        await human_delay(2, 4)

        # Check if we're redirected to login
        if "/login" in page.url or "/checkpoint" in page.url:
            logger.warning(f"LinkedIn session expired for user {user_id}")
            return False
        return True
    finally:
        await page.close()


async def validate_session_cookies(cookies: list[dict]) -> bool:
    """Validate LinkedIn session cookies by navigating to feed and checking for auth redirect."""
    import random

    from app.automation.browser_manager import get_browser

    browser = await get_browser()
    context = await browser.new_context(
        user_agent=random.choice(
            [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ]
        ),
        viewport={"width": 1920, "height": 1080},
    )
    await context.add_cookies(cookies)
    page = await context.new_page()
    try:
        await page.goto(
            "https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000
        )
        await human_delay(2, 4)
        return not ("/login" in page.url or "/checkpoint" in page.url or "/authwall" in page.url)
    except Exception as e:
        logger.warning(f"Cookie validation navigation failed: {e}")
        return False
    finally:
        await page.close()
        await context.close()


async def like_post(user_id: str, post_url: str) -> bool:
    """Navigate to a LinkedIn post and click the like button."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Liking post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Find the like button — LinkedIn uses various selectors
        like_button = await page.query_selector(
            'button[aria-label*="Like"]:not([aria-pressed="true"]), '
            'button.react-button__trigger[aria-pressed="false"]'
        )

        if like_button:
            await human_delay(0.5, 1.5)
            await like_button.click()
            await human_delay(1, 3)
            logger.info(f"Successfully liked post: {post_url}")
            return True
        else:
            # May already be liked
            already_liked = await page.query_selector(
                'button[aria-label*="Like"][aria-pressed="true"]'
            )
            if already_liked:
                logger.info(f"Post already liked: {post_url}")
                return True
            logger.warning(f"Like button not found for post: {post_url}")
            return False
    except Exception as e:
        logger.error(f"Error liking post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def comment_on_post(user_id: str, post_url: str, comment_text: str) -> bool:
    """Navigate to a LinkedIn post and add a comment."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Commenting on post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Click the comment button to open comment box
        comment_button = await page.query_selector(
            'button[aria-label*="Comment"], button.comment-button'
        )
        if comment_button:
            await comment_button.click()
            await human_delay(1, 3)

        # Find the comment input field
        comment_box = await page.wait_for_selector(
            "div.ql-editor[data-placeholder], "
            'div[role="textbox"][contenteditable="true"], '
            "div.comments-comment-box__form div[contenteditable]",
            timeout=10000,
        )

        if not comment_box:
            logger.error(f"Comment box not found for post: {post_url}")
            return False

        # Type the comment with human-like delays
        await comment_box.click()
        await human_delay(0.5, 1.0)

        for char in comment_text:
            await page.keyboard.type(char, delay=int(40 + 80 * __import__("random").random()))
            if __import__("random").random() < 0.03:
                await asyncio.sleep(__import__("random").uniform(0.2, 0.5))

        await human_delay(1, 3)

        # Click submit button
        submit_button = await page.query_selector(
            'button.comments-comment-box__submit-button, button[type="submit"][class*="comment"]'
        )

        if submit_button:
            await submit_button.click()
            await human_delay(2, 4)
            logger.info(f"Successfully commented on post: {post_url}")
            return True
        else:
            # Try pressing Ctrl+Enter as fallback
            await page.keyboard.press("Control+Enter")
            await human_delay(2, 4)
            logger.info(f"Submitted comment via keyboard shortcut on: {post_url}")
            return True

    except Exception as e:
        logger.error(f"Error commenting on post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def scrape_profile_posts(profile_url: str, cookies: list[dict] | None = None) -> list[dict]:
    """Scrape recent posts from a LinkedIn profile or company page.

    If cookies (e.g. li_at, JSESSIONID) are provided the browser context will
    be authenticated, which is far more reliable than anonymous scraping.
    """
    import random

    from app.automation.browser_manager import get_browser

    browser = await get_browser()
    context = await browser.new_context(
        user_agent=random.choice(
            [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ]
        ),
        viewport={"width": 1920, "height": 1080},
    )
    if cookies:
        await context.add_cookies(cookies)
    page = await context.new_page()

    posts = []
    try:
        # Navigate to the profile's recent activity or posts page
        posts_url = profile_url.rstrip("/")
        if "/company/" in posts_url:
            posts_url += "/posts/"
        else:
            posts_url += "/recent-activity/all/"

        await page.goto(posts_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(3, 6)

        # Auth-wall detection
        if "/login" in page.url or "/checkpoint" in page.url or "/authwall" in page.url:
            logger.warning(
                f"LinkedIn auth wall detected for {profile_url} — cookies missing or expired"
            )
            return []

        # Scroll down to load posts
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await human_delay(1, 2)

        # Extract post elements
        post_elements = await page.query_selector_all(
            'div[data-urn*="activity"], div.feed-shared-update-v2'
        )

        for element in post_elements[:10]:  # Limit to 10 most recent
            try:
                # Extract post URL
                link = await element.query_selector('a[href*="/feed/update/"]')
                post_href = await link.get_attribute("href") if link else None

                # Extract text content
                text_el = await element.query_selector(
                    '.feed-shared-update-v2__description, .update-components-text, span[dir="ltr"]'
                )
                text = await text_el.inner_text() if text_el else ""

                # Extract URN/ID
                data_urn = await element.get_attribute("data-urn")
                external_id = data_urn or post_href or ""

                if external_id:
                    posts.append(
                        {
                            "external_id": external_id,
                            "url": f"https://www.linkedin.com{post_href}"
                            if post_href and post_href.startswith("/")
                            else post_href or profile_url,
                            "content": text[:2000],  # Cap content length
                        }
                    )
            except Exception as e:
                logger.debug(f"Error extracting post element: {e}")
                continue

    except Exception as e:
        logger.error(f"Error scraping profile {profile_url}: {e}")
    finally:
        await page.close()
        await context.close()

    logger.info(f"Scraped {len(posts)} posts from {profile_url}")
    return posts

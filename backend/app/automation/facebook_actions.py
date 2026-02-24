"""Facebook Playwright automation for personal accounts."""

import asyncio
import logging
import random
import uuid

from playwright.async_api import Page

from app.automation.browser_manager import get_browser, get_page, human_delay

logger = logging.getLogger(__name__)


async def _get_user_cookies(user_id: str) -> list[dict] | None:
    """Load stored session cookies for a user's Meta integration."""
    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.integration import IntegrationAccount, Platform

    async with get_task_session() as db:
        result = await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == uuid.UUID(user_id),
                IntegrationAccount.platform == Platform.META,
            )
        )
        integration = result.scalar_one_or_none()
        if integration and integration.session_cookies:
            return integration.session_cookies
    return None


async def _get_page_for_user(user_id: str) -> Page:
    """Get a Playwright page with the user's Facebook session cookies."""
    cookies = await _get_user_cookies(user_id)
    page = await get_page(user_id, cookies)
    return page


async def like_post(user_id: str, post_url: str) -> bool:
    """Navigate to a Facebook post and click the like button."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Liking Facebook post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Facebook like button
        like_button = await page.query_selector(
            'div[aria-label="Like"]:not([aria-pressed="true"]), span[aria-label="Like"]'
        )

        if like_button:
            await human_delay(0.5, 1.5)
            await like_button.click()
            await human_delay(1, 3)
            logger.info(f"Successfully liked Facebook post: {post_url}")
            return True

        # Check if already liked
        already_liked = await page.query_selector(
            'div[aria-label="Remove Like"], div[aria-label="Like"][aria-pressed="true"]'
        )
        if already_liked:
            logger.info(f"Facebook post already liked: {post_url}")
            return True

        logger.warning(f"Like button not found for Facebook post: {post_url}")
        return False
    except Exception as e:
        logger.error(f"Error liking Facebook post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def comment_on_post(user_id: str, post_url: str, comment_text: str) -> bool:
    """Navigate to a Facebook post and add a comment."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Commenting on Facebook post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Find the comment input — Facebook uses contenteditable divs
        comment_box = await page.query_selector(
            'div[aria-label="Write a comment"], '
            'div[aria-label="Write a comment…"], '
            'div[contenteditable="true"][role="textbox"][aria-label*="comment" i]'
        )

        if not comment_box:
            # Try clicking the comment button/area to reveal the input
            comment_trigger = await page.query_selector(
                'div[aria-label="Leave a comment"], span:has-text("Write a comment")'
            )
            if comment_trigger:
                await comment_trigger.click()
                await human_delay(1, 2)
                comment_box = await page.query_selector(
                    'div[contenteditable="true"][role="textbox"]'
                )

        if not comment_box:
            logger.error(f"Comment box not found for Facebook post: {post_url}")
            return False

        await comment_box.click()
        await human_delay(0.5, 1.0)

        # Type with human-like delays
        for char in comment_text:
            await page.keyboard.type(char, delay=int(40 + 80 * random.random()))
            if random.random() < 0.03:
                await asyncio.sleep(random.uniform(0.2, 0.5))

        await human_delay(1, 3)

        # Submit with Enter key (Facebook uses Enter to submit comments)
        await page.keyboard.press("Enter")
        await human_delay(2, 4)
        logger.info(f"Successfully commented on Facebook post: {post_url}")
        return True

    except Exception as e:
        logger.error(f"Error commenting on Facebook post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def scrape_page_posts(page_url: str) -> list[dict]:
    """Scrape recent posts from a Facebook page using Playwright."""
    import re

    browser = await get_browser()
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = await context.new_page()

    posts = []
    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(3, 6)

        # Scroll to load posts
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await human_delay(1, 2)

        # Extract post links
        post_links = await page.query_selector_all(
            'a[href*="/posts/"], a[href*="permalink.php"], a[href*="/videos/"]'
        )

        seen_ids = set()
        for link in post_links[:15]:
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Extract a post identifier
                post_id = None
                posts_match = re.search(r"/posts/(\w+)", href)
                if posts_match:
                    post_id = f"post_{posts_match.group(1)}"
                else:
                    permalink_match = re.search(r"story_fbid=(\d+)", href)
                    if permalink_match:
                        post_id = f"story_{permalink_match.group(1)}"
                    else:
                        video_match = re.search(r"/videos/(\d+)", href)
                        if video_match:
                            post_id = f"video_{video_match.group(1)}"

                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                full_url = f"https://www.facebook.com{href}" if href.startswith("/") else href

                posts.append(
                    {
                        "external_id": post_id,
                        "url": full_url,
                        "content": "",
                    }
                )
            except Exception as e:
                logger.debug(f"Error extracting FB post element: {e}")
                continue

    except Exception as e:
        logger.error(f"Error scraping Facebook page {page_url}: {e}")
    finally:
        await page.close()
        await context.close()

    logger.info(f"Scraped {len(posts)} posts from Facebook page {page_url}")
    return posts

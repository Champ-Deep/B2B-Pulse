"""Instagram Playwright automation for personal accounts."""

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
    """Get a Playwright page with the user's Instagram session cookies."""
    cookies = await _get_user_cookies(user_id)
    page = await get_page(user_id, cookies)
    return page


async def like_post(user_id: str, post_url: str) -> bool:
    """Navigate to an Instagram post and click the like button."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Liking Instagram post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Instagram like button SVG — heart icon
        like_button = await page.query_selector(
            'span.fr66n button, section button svg[aria-label="Like"], svg[aria-label="Like"]'
        )

        if like_button:
            # Click the parent button if we got the SVG
            parent = await like_button.evaluate_handle("el => el.closest('button') || el")
            await parent.as_element().click()
            await human_delay(1, 3)
            logger.info(f"Successfully liked Instagram post: {post_url}")
            return True

        # Check if already liked
        already_liked = await page.query_selector(
            'svg[aria-label="Unlike"], span.fr66n button svg[fill="red"]'
        )
        if already_liked:
            logger.info(f"Instagram post already liked: {post_url}")
            return True

        logger.warning(f"Like button not found for Instagram post: {post_url}")
        return False
    except Exception as e:
        logger.error(f"Error liking Instagram post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def comment_on_post(user_id: str, post_url: str, comment_text: str) -> bool:
    """Navigate to an Instagram post and add a comment."""
    page = await _get_page_for_user(user_id)
    try:
        logger.info(f"Commenting on Instagram post: {post_url}")
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 5)

        # Find the comment textarea
        comment_box = await page.query_selector(
            'textarea[aria-label="Add a comment…"], '
            'form textarea[placeholder*="comment"], '
            'textarea[aria-label*="comment" i]'
        )

        if not comment_box:
            # Try clicking the comment icon first
            comment_icon = await page.query_selector(
                'svg[aria-label="Comment"], span._15y0l button'
            )
            if comment_icon:
                parent = await comment_icon.evaluate_handle("el => el.closest('button') || el")
                await parent.as_element().click()
                await human_delay(1, 2)
                comment_box = await page.query_selector('textarea[aria-label*="comment" i]')

        if not comment_box:
            logger.error(f"Comment box not found for Instagram post: {post_url}")
            return False

        await comment_box.click()
        await human_delay(0.5, 1.0)

        # Type with human-like delays
        for char in comment_text:
            await page.keyboard.type(char, delay=int(40 + 80 * random.random()))
            if random.random() < 0.03:
                await asyncio.sleep(random.uniform(0.2, 0.5))

        await human_delay(1, 3)

        # Find and click the Post button
        post_button = await page.query_selector(
            'button[type="submit"]:has-text("Post"), div[role="button"]:has-text("Post")'
        )

        if post_button:
            await post_button.click()
            await human_delay(2, 4)
            logger.info(f"Successfully commented on Instagram post: {post_url}")
            return True

        # Fallback: Enter key
        await page.keyboard.press("Enter")
        await human_delay(2, 4)
        logger.info(f"Submitted Instagram comment via Enter key on: {post_url}")
        return True

    except Exception as e:
        logger.error(f"Error commenting on Instagram post {post_url}: {e}")
        raise
    finally:
        await page.close()


async def scrape_profile_posts(profile_url: str) -> list[dict]:
    """Scrape recent posts from an Instagram profile using Playwright."""
    browser = await get_browser()
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = await context.new_page()

    posts = []
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(3, 6)

        # Scroll to load posts
        for _ in range(2):
            await page.evaluate("window.scrollBy(0, 600)")
            await human_delay(1, 2)

        # Extract post links from the profile grid
        post_links = await page.query_selector_all('a[href*="/p/"], a[href*="/reel/"]')

        for link in post_links[:12]:  # Limit to 12 recent posts
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Extract shortcode from URL
                import re

                match = re.search(r"/(p|reel)/([A-Za-z0-9_-]+)", href)
                if not match:
                    continue

                shortcode = match.group(2)
                full_url = f"https://www.instagram.com{href}" if href.startswith("/") else href

                posts.append(
                    {
                        "external_id": f"ig_{shortcode}",
                        "url": full_url,
                        "content": "",  # Would need to click into post to get caption
                    }
                )
            except Exception as e:
                logger.debug(f"Error extracting IG post element: {e}")
                continue

    except Exception as e:
        logger.error(f"Error scraping Instagram profile {profile_url}: {e}")
    finally:
        await page.close()
        await context.close()

    logger.info(f"Scraped {len(posts)} posts from Instagram profile {profile_url}")
    return posts

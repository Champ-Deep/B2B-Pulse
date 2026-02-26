import asyncio
import logging
import uuid

from playwright.async_api import Page

from app.automation.browser_manager import get_page, human_delay

logger = logging.getLogger(__name__)


async def _get_user_cookies(user_id: str) -> list[dict] | None:
    """Load stored session cookies for a user's LinkedIn integration."""
    import json

    from sqlalchemy import select

    from app.database import get_task_session
    from app.models.integration import IntegrationAccount, Platform
    from app.core.security import decrypt_value

    async with get_task_session() as db:
        result = await db.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == uuid.UUID(user_id),
                IntegrationAccount.platform == Platform.LINKEDIN,
            )
        )
        integration = result.scalar_one_or_none()
        if integration and integration.session_cookies:
            # Decrypt cookies if they're encrypted (encrypted cookies are strings)
            cookies_data = integration.session_cookies
            if isinstance(cookies_data, str):
                # It's encrypted
                try:
                    decrypted = decrypt_value(cookies_data)
                    return json.loads(decrypted)
                except Exception:
                    logger.warning(f"Failed to decrypt cookies for user {user_id}")
                    return None
            return cookies_data
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


async def validate_session_cookies(cookies: list[dict]) -> dict:
    """Validate LinkedIn session cookies by navigating to feed and checking for auth redirect.

    Returns a dict with:
      - valid: bool
      - user_name: str | None (display name if extracted)
      - user_id: str | None (LinkedIn user ID if extracted)
    """
    import os
    import random

    from playwright.async_api import async_playwright
    from app.automation.browser_manager import get_proxy_url

    result = {"valid": False, "user_name": None, "user_id": None}
    pw = await async_playwright().start()

    # Get proxy URL from env var
    proxy_url = get_proxy_url()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    browser = await pw.chromium.launch(
        headless=True,
        args=launch_args,
    )
    try:
        context_kwargs = {
            "user_agent": random.choice(
                [
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                ]
            ),
            "viewport": {"width": 1920, "height": 1080},
            "ignore_https_errors": True,
        }

        if proxy_url:
            context_kwargs["proxy"] = {"server": proxy_url}

        context = await browser.new_context(**context_kwargs)
        await context.add_cookies(cookies)
        page = await context.new_page()
        try:
            await page.goto(
                "https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000
            )
            await asyncio.sleep(2)

            # Check if redirected to login
            if "/login" in page.url or "/checkpoint" in page.url or "/authwall" in page.url:
                return result

            result["valid"] = True

            # Extract user info from the page
            try:
                # Try to find the profile name in the nav
                profile_link = await page.query_selector(
                    'a[href*="/in/"][data-test-nav-top-bar-profile-dropdown]'
                )
                if not profile_link:
                    profile_link = await page.query_selector('button[aria-label*="Profile"]')
                if not profile_link:
                    profile_link = await page.query_selector(".feed-shared-update-v2__actor-meta a")

                if profile_link:
                    user_name = await profile_link.inner_text()
                    if user_name:
                        result["user_name"] = user_name.strip().split("\n")[0]
            except Exception as e:
                logger.debug(f"Could not extract user name: {e}")

            # Try to get user ID from cookies
            try:
                all_cookies = await context.cookies()
                for cookie in all_cookies:
                    if cookie["name"] == "li_at":
                        # The li_at value contains user info, but it's a JWT-like token
                        # We can try to decode it to get user ID
                        pass
            except Exception:
                pass

            return result
        except Exception as e:
            logger.warning(f"Cookie validation navigation failed: {e}")
            return result
        finally:
            await page.close()
            await context.close()
    finally:
        await browser.close()
        await pw.stop()


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

    Launches a fresh Playwright browser per invocation to avoid state pollution
    from any shared browser_manager.
    """
    import random

    from playwright.async_api import async_playwright
    from app.automation.browser_manager import get_proxy_url

    posts = []
    ua = random.choice(
        [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ]
    )

    pw = await async_playwright().start()

    # Get proxy URL from env var
    proxy_url = get_proxy_url()

    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    try:
        context_kwargs = {
            "user_agent": ua,
            "viewport": {"width": 1920, "height": 1080},
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "ignore_https_errors": True,
        }

        if proxy_url:
            context_kwargs["proxy"] = {"server": proxy_url}

        context = await browser.new_context(**context_kwargs)
        # Inject cookies BEFORE the first navigation so the session is recognised
        if cookies:
            await context.add_cookies(cookies)
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        page = await context.new_page()

        posts_url = profile_url.rstrip("/")
        if "/company/" in posts_url:
            posts_url += "/posts/"
        else:
            posts_url += "/recent-activity/all/"

        try:
            await page.goto(posts_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
        except Exception as nav_err:
            logger.warning(f"Navigation to {posts_url} failed: {nav_err}")
            return []

        final_url = page.url
        logger.debug(f"Final URL: {final_url}")
        if "/login" in final_url or "/checkpoint" in final_url or "/authwall" in final_url:
            logger.warning(
                f"LinkedIn auth wall detected for {profile_url} — "
                "li_at cookie may be expired. Go to Settings → LinkedIn → re-login."
            )
            return []

        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(1)

        post_elements = await page.query_selector_all(
            'div[data-urn*="activity"], '
            "li.profile-creator-shared-feed-update__container, "
            "div.feed-shared-update-v2, "
            "div.occludable-update"
        )
        logger.info(f"Found {len(post_elements)} post elements on {profile_url}")

        for element in post_elements[:10]:
            try:
                link = await element.query_selector('a[href*="/feed/update/"]')
                post_href = await link.get_attribute("href") if link else None
                text_el = await element.query_selector(
                    ".feed-shared-update-v2__description, "
                    ".update-components-text, "
                    'span[dir="ltr"], '
                    ".attributed-text-segment-list__content"
                )
                text = await text_el.inner_text() if text_el else ""
                data_urn = await element.get_attribute("data-urn")
                external_id = data_urn or post_href or ""
                if external_id:
                    posts.append(
                        {
                            "external_id": external_id,
                            "url": (
                                f"https://www.linkedin.com{post_href}"
                                if post_href and post_href.startswith("/")
                                else post_href or profile_url
                            ),
                            "content": text[:2000],
                        }
                    )
            except Exception as e:
                logger.debug(f"Error extracting post element: {e}")

    except Exception as e:
        logger.error(f"Error scraping profile {profile_url}: {e}")
    finally:
        await browser.close()
        await pw.stop()

    logger.info(f"Scraped {len(posts)} posts from {profile_url}")
    return posts

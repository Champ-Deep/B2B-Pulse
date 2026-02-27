import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

CONTEXT_TTL_SECONDS = 600  # 10 minutes - contexts expire after inactivity
CONTEXT_MAX_AGE_SECONDS = 1800  # 30 minutes - hard limit to prevent memory leaks

# Global browser instance (shared across tasks in the worker process)
_browser: Browser | None = None
_playwright = None

# User contexts keyed by user_id with metadata
_contexts: dict[str, "UserContext"] = {}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def get_proxy_url(user_proxy: str | None = None) -> str | None:
    """Get proxy URL from user override or global env var.

    Args:
        user_proxy: Optional per-user proxy URL override

    Returns:
        Proxy URL string or None if no proxy configured
    """
    # User-provided proxy takes priority
    if user_proxy:
        return user_proxy

    # Fall back to global env var
    return os.getenv("BROWSER_PROXY_URL")


@dataclass
class UserContext:
    """Wrapper for browser context with metadata."""

    context: BrowserContext
    created_at: float
    last_used_at: float
    user_id: str


async def _cleanup_expired_contexts():
    """Remove expired contexts to prevent memory leaks."""
    current_time = time.time()
    expired_users = []

    for user_id, user_ctx in _contexts.items():
        age = current_time - user_ctx.created_at
        idle_time = current_time - user_ctx.last_used_at

        # Evict if: too old (hard limit) OR idle for too long
        if age > CONTEXT_MAX_AGE_SECONDS or idle_time > CONTEXT_TTL_SECONDS:
            expired_users.append(user_id)

    for user_id in expired_users:
        await close_user_context(user_id)
        logger.info(f"Evicted expired context for user {user_id}")


async def get_browser() -> Browser:
    """Get or create the shared browser instance."""
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        proxy_url = get_proxy_url()
        launch_kwargs: dict = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        }
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}
        _browser = await _playwright.chromium.launch(**launch_kwargs)
        logger.info(f"Browser instance created (proxy={'yes' if proxy_url else 'no'})")
    return _browser


STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
]


async def get_context(
    user_id: str,
    cookies: list[dict] | None = None,
    proxy: str | None = None,
) -> BrowserContext:
    """Get or create a browser context for a specific user.

    Args:
        user_id: The user's ID
        cookies: Optional cookies to set. If context exists, cookies will be updated
                 to reflect any changes (e.g., after user re-authenticates).
        proxy: Optional proxy URL (format: protocol://user:pass@host:port).
               Falls back to BROWSER_PROXY_URL env var if not provided.

    Returns:
        The browser context for the user.
    """
    # Cleanup expired contexts periodically
    await _cleanup_expired_contexts()

    current_time = time.time()

    # Get proxy URL - user parameter takes priority, then env var
    proxy_url = proxy or get_proxy_url()

    if user_id in _contexts:
        user_ctx = _contexts[user_id]
        try:
            # Verify context is still valid
            _ = user_ctx.context.pages

            # Update cookies if provided (handles cookie refresh after re-auth)
            if cookies:
                try:
                    # Clear existing cookies and set new ones
                    await user_ctx.context.clear_cookies()
                    await user_ctx.context.add_cookies(cookies)
                    logger.debug(f"Updated cookies for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to update cookies for {user_id}: {e}")
                    # If cookie update fails, recreate context
                    await user_ctx.context.close()
                    del _contexts[user_id]

            # Update last used time
            user_ctx.last_used_at = current_time
            return user_ctx.context

        except Exception:
            # Context is invalid, remove it
            if user_id in _contexts:
                del _contexts[user_id]

    browser = await get_browser()

    # Build context kwargs
    context_kwargs = {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }

    # Add proxy if configured
    if proxy_url:
        context_kwargs["proxy"] = {"server": proxy_url}
        logger.info(f"Using proxy {proxy_url} for user {user_id}")

    context = await browser.new_context(**context_kwargs)

    # Inject stealth scripts
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)

    # Set cookies if provided
    if cookies:
        await context.add_cookies(cookies)

    _contexts[user_id] = UserContext(
        context=context,
        created_at=current_time,
        last_used_at=current_time,
        user_id=user_id,
    )
    logger.info(f"Browser context created for user {user_id}")
    return context


async def get_page(user_id: str, cookies: list[dict] | None = None) -> Page:
    """Get a new page in the user's browser context.

    Args:
        user_id: The user's ID
        cookies: Optional cookies to set. Will update existing context if provided.

    Returns:
        A new page in the user's context.
    """
    context = await get_context(user_id, cookies)
    page = await context.new_page()
    return page


async def human_delay(min_seconds: float = 1.0, max_seconds: float = 3.0):
    """Add a human-like random delay."""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)


async def human_type(page: Page, selector: str, text: str):
    """Type text with human-like delays between keystrokes."""
    element = await page.wait_for_selector(selector, timeout=10000)
    if element:
        for char in text:
            await element.type(char, delay=random.randint(30, 120))
            if random.random() < 0.05:  # 5% chance of a longer pause
                await asyncio.sleep(random.uniform(0.3, 0.8))


async def close_user_context(user_id: str):
    """Close a specific user's browser context."""
    if user_id in _contexts:
        try:
            await _contexts[user_id].context.close()
        except Exception as e:
            logger.warning(f"Error closing context for {user_id}: {e}")
        finally:
            del _contexts[user_id]
            logger.info(f"Closed browser context for user {user_id}")


async def close_context_after_use(user_id: str):
    """Close a user's context after engagement action completes.

    This ensures no stale state and forces fresh cookie check on next use.
    Use this after engagement actions that modify state (login, etc).
    """
    await close_user_context(user_id)


async def shutdown_browser():
    """Gracefully shut down the browser and all contexts."""
    global _browser, _playwright
    for user_id, user_ctx in _contexts.items():
        try:
            await user_ctx.context.close()
        except Exception as e:
            logger.warning(f"Error closing context for {user_id}: {e}")
    _contexts.clear()
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    logger.info("Browser shutdown complete")

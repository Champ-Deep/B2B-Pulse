import asyncio
import logging
import random

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

# Global browser instance (shared across tasks in the worker process)
_browser: Browser | None = None
_playwright = None

# User contexts keyed by user_id
_contexts: dict[str, BrowserContext] = {}

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


async def get_browser() -> Browser:
    """Get or create the shared browser instance."""
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=STEALTH_ARGS,
        )
        logger.info("Browser instance created")
    return _browser


async def get_context(user_id: str, cookies: list[dict] | None = None) -> BrowserContext:
    """Get or create a browser context for a specific user."""
    if user_id in _contexts:
        try:
            # Verify context is still valid (accessing .pages throws if closed)
            _ = _contexts[user_id].pages
            return _contexts[user_id]
        except Exception:
            del _contexts[user_id]

    browser = await get_browser()
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
    )

    # Inject stealth scripts
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)

    # Restore cookies if provided
    if cookies:
        await context.add_cookies(cookies)

    _contexts[user_id] = context
    logger.info(f"Browser context created for user {user_id}")
    return context


async def get_page(user_id: str, cookies: list[dict] | None = None) -> Page:
    """Get a new page in the user's browser context."""
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
        await _contexts[user_id].close()
        del _contexts[user_id]


async def shutdown_browser():
    """Gracefully shut down the browser."""
    global _browser, _playwright
    for context in _contexts.values():
        await context.close()
    _contexts.clear()
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None

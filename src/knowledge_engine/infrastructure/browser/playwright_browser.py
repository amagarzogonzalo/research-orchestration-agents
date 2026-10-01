"""PlaywrightBrowser — full browser automation over Playwright (SOTA, async).

Implements :class:`~knowledge_engine.core.ports.BrowserPort` and a
:class:`~knowledge_engine.core.ports.BrowserActionPort` so an agent can
navigate, click, fill, submit forms, extract text/markdown, screenshot, and
assert visibility — enabling web scraping, form submission, and UI testing.

Lazy-imports Playwright so the ``[browser]`` extra stays optional; a single
headed-headless Chromium is launched per adapter instance and closed on
:meth:`close`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from knowledge_engine.core.ports import BrowserActionPort, BrowserPort
from knowledge_engine.schemas.browser import BrowserActionResult

logger = logging.getLogger(__name__)


class PlaywrightBrowser(BrowserPort, BrowserActionPort):
    """Chromium automation via Playwright with a shared async page handle.

    Concurrency contract: one adapter instance is one logical browser session.
    Concurrent :meth:`act` calls serialize safely on an internal lock; for
    truly parallel sessions, create one adapter instance per task.
    """

    def __init__(self, *, headless: bool = True, navigation_timeout_ms: int = 30_000) -> None:
        """Create a PlaywrightBrowser.

        Args:
            headless: Whether to run headless.
            navigation_timeout_ms: Navigation timeout in milliseconds.
        """
        self._headless = headless
        self._navigation_timeout_ms = navigation_timeout_ms
        self._pw = None
        self._browser = None
        self._page = None
        self._page_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------
    async def _ensure_page(self) -> Any:
        """Return the shared page, launching Chromium on first use.

        Returns:
            Any: Shared Playwright page handle.
        """
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._page = await self._browser.new_page()
        return self._page

    async def close(self) -> None:
        """Close the browser + Playwright driver, releasing resources."""
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._page = None

    # -- BrowserPort ---------------------------------------------------------
    async def navigate(self, url: str, *, wait_selector: str = "body") -> BrowserActionResult:
        """Navigate.

        Args:
            url: Target URL.
            wait_selector: Selector to wait for.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        await page.goto(url, timeout=self._navigation_timeout_ms, wait_until="domcontentloaded")
        await page.wait_for_selector(wait_selector, state="visible")
        return BrowserActionResult.success("navigate", url=page.url)

    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Click.

        Args:
            selector: CSS selector.
            timeout_ms: Timeout in milliseconds.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        await page.click(selector, timeout=timeout_ms)
        return BrowserActionResult.success("click", url=page.url, meta={"selector": selector})

    async def fill(self, selector: str, value: str) -> BrowserActionResult:
        """Fill.

        Args:
            selector: CSS selector.
            value: Channel value.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        await page.fill(selector, value)
        return BrowserActionResult.success("fill", url=page.url, meta={"selector": selector})

    async def submit(self, selector: str | None = None) -> BrowserActionResult:
        """Submit a request.

        Args:
            selector: CSS selector.

        Returns:
            BrowserActionResult: Action outcome.

        """
        page = await self._ensure_page()
        if selector:
            await page.click(selector)
        else:
            await page.keyboard.press("Enter")
        return BrowserActionResult.success("submit", url=page.url, meta={"selector": selector})

    async def extract_text(self, selector: str = "body") -> BrowserActionResult:
        """Extract text.

        Args:
            selector: CSS selector.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        text = await page.locator(selector).inner_text()
        return BrowserActionResult.success("extract_text", url=page.url, text=text, meta={"selector": selector})

    async def extract_markdown(self) -> BrowserActionResult:
        """Extract markdown.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        html = await page.content()
        text = await page.locator("body").inner_text()
        markdown = _html_to_markdown(html, text)
        return BrowserActionResult.success("extract_markdown", url=page.url, markdown=markdown)

    async def screenshot(self, path: str | None = None) -> BrowserActionResult:
        """Screenshot.

        Args:
            path: File path.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        file_path = path or f"screenshot_{page.url.replace('://', '_').replace('/', '_').rstrip('_')}.png"
        await page.screenshot(path=file_path, full_page=True)
        return BrowserActionResult.success("screenshot", url=page.url, screenshot_path=file_path)

    async def assert_visible(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Assert visible.

        Args:
            selector: CSS selector.
            timeout_ms: Timeout in milliseconds.

        Returns:
            BrowserActionResult: Action outcome.
        """
        page = await self._ensure_page()
        await page.locator(selector).wait_for(state="visible", timeout=timeout_ms)
        return BrowserActionResult.success("assert_visible", url=page.url, meta={"selector": selector})

    # -- BrowserActionPort ---------------------------------------------------
    _ACTIONS = {
        "navigate",
        "click",
        "fill",
        "submit",
        "extract_text",
        "extract_markdown",
        "screenshot",
        "assert_visible",
    }

    async def act(self, action: str, **kwargs: Any) -> BrowserActionResult:
        """Dispatch a named browser action to its handler method.

                The page lock is held for the whole action, so concurrent ``act()``
                calls from parallel tasks serialize instead of interleaving clicks,
                fills, and navigations on the shared page.

        Args:
            action: Browser action name.
            **kwargs: Action-specific arguments.

        Returns:
            BrowserActionResult: Action outcome.

        """
        if action not in self._ACTIONS:
            return BrowserActionResult.failure(action, f"unknown action {action!r}")
        method = getattr(self, action)
        try:
            async with self._page_lock:
                return await method(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface as a typed failure
            logger.exception("browser action %s failed", action)
            return BrowserActionResult.failure(action, str(exc))


def _html_to_markdown(html: str, text: str) -> str:
    """Html to markdown.

    Args:
        html: HTML content.
        text: Encoded text.

    Returns:
        str: Result text.

    """
    del html  # pure-text fallback (crawl4ai renders real markdown upstream)
    return text.strip()


__all__ = ["PlaywrightBrowser"]

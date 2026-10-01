"""SeleniumBrowser — full browser automation over Selenium (fallback tier).

Implements the same :class:`~knowledge_engine.core.ports.BrowserPort` and
:class:`~knowledge_engine.core.ports.BrowserActionPort` contracts as Playwright
but drives Selenium's WebDriver instead. Kept as an equivalent adapter so a
project that already has driver infrastructure (or hits a CDP-incompatible
site) can swap it in without touching agent or node code.

Lazy-imports Selenium so the ``[browser]`` extra stays optional.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from knowledge_engine.core.ports import BrowserActionPort, BrowserPort
from knowledge_engine.schemas.browser import BrowserActionResult

logger = logging.getLogger(__name__)

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


class SeleniumBrowser(BrowserPort, BrowserActionPort):
    """WebDriver automation via Selenium with lazily-created driver/context.

    Concurrency contract: one adapter instance is one logical browser session.
    Concurrent :meth:`act` calls serialize safely on an internal lock; for
    truly parallel sessions, create one adapter instance per task. Note the
    underlying WebDriver calls block, so a busy instance also blocks the loop.
    """

    def __init__(self, *, headless: bool = True, driver: Any | None = None) -> None:
        self._headless = headless
        self._driver = driver
        self._owns_driver = driver is None
        self._driver_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------
    def _ensure_driver(self):
        """Return a configured WebDriver, creating one if not injected."""
        if self._driver is not None:
            return self._driver
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--headless" if self._headless else "--no-sandbox")
        options.add_argument("--no-sandbox")
        self._driver = webdriver.Chrome(options=options)
        return self._driver

    async def close(self) -> None:
        """Quit the WebDriver if it was created by this adapter."""
        if self._driver is not None and self._owns_driver:
            self._driver.quit()
        self._driver = None

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _visible(driver) -> bool:
        try:
            return bool(driver.execute_script("return document.visibilityState == 'visible'"))
        except Exception:  # noqa: BLE001 - best-effort
            return True

    # -- BrowserPort ---------------------------------------------------------
    async def navigate(self, url: str, *, wait_selector: str = "body") -> BrowserActionResult:
        driver = self._ensure_driver()
        driver.get(url)
        for _ in range(30):  # poll ~10s for the selector
            if SeleniumBrowser._visible(driver):
                break
            time.sleep(0.3)
        return BrowserActionResult.success("navigate", url=driver.current_url)

    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        from selenium.webdriver.common.by import By

        driver = self._ensure_driver()
        driver.find_element(By.CSS_SELECTOR, selector).click()
        return BrowserActionResult.success("click", url=driver.current_url, meta={"selector": selector})

    async def fill(self, selector: str, value: str) -> BrowserActionResult:
        from selenium.webdriver.common.by import By

        driver = self._ensure_driver()
        field = driver.find_element(By.CSS_SELECTOR, selector)
        field.clear()
        field.send_keys(value)
        return BrowserActionResult.success("fill", url=driver.current_url, meta={"selector": selector})

    async def submit(self, selector: str | None = None) -> BrowserActionResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        driver = self._ensure_driver()
        if selector:
            driver.find_element(By.CSS_SELECTOR, selector).click()
        else:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RETURN)
        return BrowserActionResult.success("submit", url=driver.current_url, meta={"selector": selector})

    async def extract_text(self, selector: str = "body") -> BrowserActionResult:
        from selenium.webdriver.common.by import By

        driver = self._ensure_driver()
        text = driver.find_element(By.CSS_SELECTOR, selector).text
        return BrowserActionResult.success(
            "extract_text", url=driver.current_url, text=text, meta={"selector": selector}
        )

    async def extract_markdown(self) -> BrowserActionResult:
        from selenium.webdriver.common.by import By

        driver = self._ensure_driver()
        text = driver.find_element(By.TAG_NAME, "body").text
        return BrowserActionResult.success("extract_markdown", url=driver.current_url, markdown=text.strip())

    async def screenshot(self, path: str | None = None) -> BrowserActionResult:
        driver = self._ensure_driver()
        file_path = path or "screenshot.png"
        driver.save_screenshot(file_path)
        return BrowserActionResult.success("screenshot", url=driver.current_url, screenshot_path=file_path)

    async def assert_visible(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        driver = self._ensure_driver()
        WebDriverWait(driver, timeout_ms / 1000).until(ec.visibility_of_element_located((By.CSS_SELECTOR, selector)))
        return BrowserActionResult.success("assert_visible", url=driver.current_url, meta={"selector": selector})

    # -- BrowserActionPort ---------------------------------------------------
    async def act(self, action: str, **kwargs: Any) -> BrowserActionResult:
        """Dispatch a named browser action to Selenium (serialized per instance)."""
        if action not in _ACTIONS:
            return BrowserActionResult.failure(action, f"unknown action {action!r}")
        method = getattr(self, action)
        try:
            async with self._driver_lock:
                return await method(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface as a typed failure
            logger.exception("selenium action %s failed", action)
            return BrowserActionResult.failure(action, str(exc))


__all__ = ["SeleniumBrowser"]

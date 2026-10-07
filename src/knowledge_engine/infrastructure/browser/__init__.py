"""Browser automation adapters (Playwright primary, Selenium fallback).

Both implement the same :class:`~knowledge_engine.core.ports.BrowserPort` /
:class:`~knowledge_engine.core.ports.BrowserActionPort` contracts, so agents
and tests depend on ports, never on a concrete browser.
"""

from __future__ import annotations

from knowledge_engine.infrastructure.browser.playwright_browser import PlaywrightBrowser
from knowledge_engine.infrastructure.browser.selenium_browser import SeleniumBrowser

__all__ = ["PlaywrightBrowser", "SeleniumBrowser"]

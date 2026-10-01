"""Playwright scraping adapter — full browser render for JS-heavy pages.

The professional tier between crawl4ai (fast heuristic Markdown) and trafilatura
(browserless static): drives a real Chromium via Playwright with explicit
selector/content waiting, then extracts LLM-ready text. Useful when the primary
renderer fails on SPA/JS-gated content.
"""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.schemas.source import DocumentBody, FetchMethod


class PlaywrightAdapter(ScrapePort):
    def __init__(self, *, timeout_ms: int = 30_000, wait_selector: str = "body") -> None:
        self.timeout_ms = timeout_ms
        self.wait_selector = wait_selector

    async def to_markdown(self, url: str) -> DocumentBody:
        try:
            from playwright.async_api import async_playwright  # lazy: keeps dep optional
        except ImportError as exc:  # pragma: no cover - guarded in extras
            raise ScrapeError("playwright not installed; add `[scrape]` extra") from exc

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                    await page.wait_for_selector(self.wait_selector, timeout=self.timeout_ms, state="visible")
                    content = await page.evaluate("() => document.body.innerText")
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001 - wrap Playwright failures
            raise ScrapeError(f"Playwright failed for {url}: {exc}") from exc

        text = (content or "").strip()
        return DocumentBody(
            url=url,
            title=_fallback_title(url),
            markdown=text,
            char_count=len(text),
            crawl_method=FetchMethod.PLAYWRIGHT,
            captured_at=datetime.now(UTC).isoformat(),
        )


def _fallback_title(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1] or url


__all__ = ["PlaywrightAdapter"]

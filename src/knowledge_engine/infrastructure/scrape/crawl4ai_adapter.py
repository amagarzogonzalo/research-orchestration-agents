"""Crawl4AI scraping adapter.

crawl4ai 0.9.x renders with Playwright and converts messy DOMs to LLM-ready
Markdown via heuristic pre-processing — reducing token bloat before the model
ever sees the content (which is the whole point on rate-limited free tiers).
"""

from __future__ import annotations

from datetime import UTC, datetime

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.schemas.source import DocumentBody, FetchMethod


class Crawl4aiAdapter(ScrapePort):
    def __init__(self, *, timeout: int = 30) -> None:
        self.timeout = timeout
        self._async_webcrawler = None

    def _crawler(self):
        if self._async_webcrawler is None:  # lazy import keeps heavy dep optional
            from crawl4ai import AsyncWebCrawler

            self._async_webcrawler = AsyncWebCrawler
        return self._async_webcrawler

    async def to_markdown(self, url: str) -> DocumentBody:
        AsyncWebCrawler = self._crawler()
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url, cache_mode="bypass")
        except Exception as exc:  # noqa: BLE001 - wrap crawl4ai/Playwright failures
            raise ScrapeError(f"Crawl4AI failed for {url}: {exc}") from exc
        markdown = (result.markdown or "") if result else ""
        title = (result.metadata.get("title") if result and result.metadata else "") or _fallback_title(url)
        return DocumentBody(
            url=url,
            title=title,
            markdown=markdown,
            char_count=len(markdown),
            crawl_method=FetchMethod.CRAWL4AI,
            captured_at=datetime.now(UTC).isoformat(),
        )


def _fallback_title(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1] or url

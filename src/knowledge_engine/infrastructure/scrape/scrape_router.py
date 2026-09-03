"""ScrapeRouter: crawl4ai primary, trafilatura fallback.
Prefer crawl4ai's LLM-ready Markdown; fall back to trafilatura on any failure.
"""

from __future__ import annotations

import logging

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.crawl4ai_adapter import Crawl4aiAdapter
from knowledge_engine.infrastructure.scrape.trafilatura_adapter import TrafilaturaAdapter
from knowledge_engine.schemas.source import DocumentBody

logger = logging.getLogger(__name__)


class ScrapeRouter(ScrapePort):
    def __init__(self, primary: ScrapePort | None = None, fallback: ScrapePort | None = None) -> None:
        self.primary = primary or Crawl4aiAdapter()
        self.fallback = fallback or TrafilaturaAdapter()

    async def to_markdown(self, url: str) -> DocumentBody:
        try:
            return await self.primary.to_markdown(url)
        except ScrapeError as exc:
            logger.warning("primary scraper failed for %s (%s); falling back", url, exc)
        return await self.fallback.to_markdown(url)

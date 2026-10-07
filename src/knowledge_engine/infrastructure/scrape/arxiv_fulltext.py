"""arXiv full-text adapter — ar5iv HTML instead of the abs landing page.

``arxiv.org/abs`` pages carry only the abstract, so scraping them yields thin
bodies. ar5iv renders the same paper as HTML; delegating extraction to the
browserless Trafilatura adapter keeps this fast and unmetered. Non-arXiv URLs
raise :class:`ScrapeError` immediately (no network) so the chain moves on.

Live probe 2026-10-05: ``https://ar5iv.org/html/2412.11919`` extracts the
full paper text via Trafilatura.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from knowledge_engine.core.exceptions import ScrapeSkipped
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.trafilatura_adapter import TrafilaturaAdapter
from knowledge_engine.schemas.source import DocumentBody, FetchMethod

logger = logging.getLogger(__name__)

_ABS_RE = re.compile(r"arxiv\.org/abs/([^/?#]+)", re.IGNORECASE)


class ArxivFullTextAdapter(ScrapePort):
    """Full text for arXiv papers via their ar5iv HTML rendering.

    Attributes:
        name: Adapter name.
    """

    name = "arxiv_fulltext"

    def __init__(self, extractor: ScrapePort | None = None, *, timeout: float = 20.0) -> None:
        """Create an ArxivFullTextAdapter.

        Args:
            extractor: HTML extractor for the ar5iv page (defaults to Trafilatura).
            timeout: Request timeout in seconds for the default extractor.
        """
        self._extractor = extractor or TrafilaturaAdapter(timeout=timeout)

    @staticmethod
    def _ar5iv_url(url: str) -> str | None:
        """Map an arXiv abs URL to its ar5iv HTML rendering.

        Args:
            url: Candidate arXiv abs URL.

        Returns:
            str | None: ar5iv HTML URL, or None for non-arXiv URLs.
        """
        match = _ABS_RE.search(url or "")
        if not match:
            return None
        return f"https://ar5iv.org/html/{match.group(1)}"

    async def to_markdown(self, url: str) -> DocumentBody:
        """Fetch the full text for an arXiv abs page.

        Args:
            url: arXiv abs page URL.

        Returns:
            DocumentBody: Full-text body tagged with the original abs URL.

        Raises:
            ScrapeSkipped: When the URL is not an arXiv abs page.
                Extraction failures propagate from the delegate extractor.
        """
        target = self._ar5iv_url(url)
        if target is None:
            raise ScrapeSkipped(f"not an arXiv abs URL: {url}")
        body = await self._extractor.to_markdown(target)
        return DocumentBody(
            url=url,
            title=body.title,
            markdown=body.markdown,
            char_count=body.char_count,
            crawl_method=FetchMethod.API,
            captured_at=datetime.now(UTC).isoformat(),
        )

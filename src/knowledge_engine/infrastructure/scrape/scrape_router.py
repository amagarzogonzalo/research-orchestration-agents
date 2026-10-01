"""ScrapeRouter: ordered injectable chain with a content-quality guard.

Default chain is crawl4ai → trafilatura → Jina Reader: local extractors first
(free, unmetered), the remote reader last (keyless but rate-limited) for pages
local fetch cannot retrieve. Pass an explicit ``chain`` to reorder or extend —
e.g. insert the Playwright adapter ahead of trafilatura for JS-heavy pages.

``looks_clean`` rejects binary PDF dumps, empty extractions and mojibake so a
"successful" fetch that yields no usable text counts as a miss, not a body.
"""

from __future__ import annotations

import logging

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.crawl4ai_adapter import Crawl4aiAdapter
from knowledge_engine.infrastructure.scrape.jina_reader import JinaReaderAdapter
from knowledge_engine.infrastructure.scrape.trafilatura_adapter import TrafilaturaAdapter
from knowledge_engine.schemas.source import DocumentBody

logger = logging.getLogger(__name__)

_MIN_CHARS = 200
_MAX_REPLACEMENT_RATIO = 0.02


def looks_clean(text: str) -> bool:
    """True when extracted text is usable LLM input.

    Args:
        text: Candidate extracted Markdown.

    Returns:
        bool: False for empty/short text, NUL bytes, or mojibake.
    """
    if not text or not text.strip():
        return False
    if "\x00" in text:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_CHARS:
        return False
    if stripped.count("\ufffd") / len(stripped) > _MAX_REPLACEMENT_RATIO:
        return False
    return True


class ScrapeRouter(ScrapePort):
    """Try each scraper in order; first clean extraction wins."""

    def __init__(self, chain: list[ScrapePort] | None = None) -> None:
        if chain is not None and not chain:
            raise ValueError("ScrapeRouter needs a non-empty chain")
        self.chain: list[ScrapePort] = chain if chain is not None else _default_chain()

    async def to_markdown(self, url: str) -> DocumentBody:
        """Return the first clean extraction, else raise.

        Args:
            url: Page URL to render.

        Returns:
            DocumentBody: The first extraction passing :func:`looks_clean`.

        Raises:
            ScrapeError: If every scraper in the chain fails or yields unusable text.
        """
        for scraper in self.chain:
            try:
                body = await scraper.to_markdown(url)
            except ScrapeError as exc:
                logger.warning("scraper %s failed for %s (%s)", type(scraper).__name__, url, exc)
                continue
            if looks_clean(body.markdown):
                return body
            logger.warning("scraper %s yielded unusable text for %s", type(scraper).__name__, url)
        raise ScrapeError(f"all scrapers failed for {url}")


def _default_chain() -> list[ScrapePort]:
    """Local-first default; remote reader last (metered, rate-limited)."""
    return [Crawl4aiAdapter(), TrafilaturaAdapter(), JinaReaderAdapter()]

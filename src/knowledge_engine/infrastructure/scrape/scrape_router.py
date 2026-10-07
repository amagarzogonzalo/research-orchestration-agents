"""ScrapeRouter: ordered injectable chain with a content-quality guard.

Default chain is source-specific full text first (ar5iv HTML carries the
whole paper; Europe PMC fullTextXML serves the OA subset as JATS), then local
extractors (free, unmetered), the remote reader last (keyless but
rate-limited) for pages local fetch cannot retrieve. Pass an explicit
``chain`` to reorder or extend — e.g. insert the Playwright adapter ahead of
trafilatura for JS-heavy pages.

``looks_clean`` rejects binary PDF dumps, empty extractions and mojibake so a
"successful" fetch that yields no usable text counts as a miss, not a body.
"""

from __future__ import annotations

import logging

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential, wait_random

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.arxiv_fulltext import ArxivFullTextAdapter
from knowledge_engine.infrastructure.scrape.crawl4ai_adapter import Crawl4aiAdapter
from knowledge_engine.infrastructure.scrape.europe_pmc_fulltext import EuropePmcFullTextAdapter
from knowledge_engine.infrastructure.scrape.jina_reader import JinaReaderAdapter
from knowledge_engine.infrastructure.scrape.trafilatura_adapter import TrafilaturaAdapter
from knowledge_engine.schemas.source import DocumentBody

logger = logging.getLogger(__name__)

_MIN_CHARS = 200
_MAX_REPLACEMENT_RATIO = 0.02


def _is_transient_scrape(exc: BaseException) -> bool:
    """Whether a scrape failure is worth retrying.

    Args:
        exc: Caught exception.

    Returns:
        bool: True for transient ScrapeErrors.
    """
    return isinstance(exc, ScrapeError) and exc.transient


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
        """Create a ScrapeRouter.

        Args:
            chain: Exception chain.

        Raises:
            ValueError: When the operation fails.

        """
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
                body = await _fetch_with_retry(scraper, url)
            except ScrapeError as exc:
                logger.warning("scraper %s failed for %s (%s)", type(scraper).__name__, url, exc)
                continue
            if looks_clean(body.markdown):
                return body
            logger.warning("scraper %s yielded unusable text for %s", type(scraper).__name__, url)
        raise ScrapeError(f"all scrapers failed for {url}")


@retry(
    wait=wait_exponential(multiplier=0.5, max=8) + wait_random(0, 1),
    stop=stop_after_attempt(3),
    retry=retry_if_exception(_is_transient_scrape),
    reraise=True,
)
async def _fetch_with_retry(scraper: ScrapePort, url: str) -> DocumentBody:
    """Fetch with backoff on transient failures; permanent errors pass through.

    Args:
        scraper: Scrape adapter.
        url: Page URL to render.

    Returns:
        DocumentBody: Fetched Markdown body.
    """
    return await scraper.to_markdown(url)


def _default_chain() -> list[ScrapePort]:
    """Source-specific full text first, then local-first fallbacks.

    Returns:
        list[ScrapePort]: Scrape adapters.

    """
    return [
        ArxivFullTextAdapter(),
        EuropePmcFullTextAdapter(),
        Crawl4aiAdapter(),
        TrafilaturaAdapter(),
        JinaReaderAdapter(),
    ]

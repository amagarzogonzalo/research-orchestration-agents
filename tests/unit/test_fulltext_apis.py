"""Unit tests: arXiv full-text scrape adapter (offline, stubbed extractor)."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.arxiv_fulltext import ArxivFullTextAdapter
from knowledge_engine.schemas.source import DocumentBody, FetchMethod


class _FakeExtractor(ScrapePort):
    """Stub extractor recording the URL it was asked to fetch."""

    def __init__(self, markdown: str = "full paper text here") -> None:
        self.seen: list[str] = []
        self.markdown = markdown

    async def to_markdown(self, url: str) -> DocumentBody:
        """Record the URL and return a canned body."""
        self.seen.append(url)
        return DocumentBody(url=url, title="t", markdown=self.markdown, char_count=len(self.markdown))


async def test_arxiv_abs_maps_to_ar5iv_and_retags() -> None:
    """Abs pages delegate to ar5iv HTML; the body keeps the original abs URL."""
    extractor = _FakeExtractor()
    body = await ArxivFullTextAdapter(extractor=extractor).to_markdown("https://arxiv.org/abs/2412.11919")
    assert extractor.seen == ["https://ar5iv.org/html/2412.11919"]
    assert body.url == "https://arxiv.org/abs/2412.11919"
    assert body.markdown == "full paper text here"
    assert body.crawl_method is FetchMethod.API


async def test_arxiv_non_arxiv_url_skips_without_network() -> None:
    """Non-arXiv URLs raise before any fetch."""
    extractor = _FakeExtractor()
    with pytest.raises(ScrapeError):
        await ArxivFullTextAdapter(extractor=extractor).to_markdown("https://europepmc.org/article/MED/1")
    assert extractor.seen == []


async def test_arxiv_extraction_failure_propagates() -> None:
    """Extractor failures fall through to the rest of the chain."""

    class _Boom(ScrapePort):
        async def to_markdown(self, url: str) -> DocumentBody:
            raise ScrapeError("nope")

    with pytest.raises(ScrapeError):
        await ArxivFullTextAdapter(extractor=_Boom()).to_markdown("https://arxiv.org/abs/2412.11919")

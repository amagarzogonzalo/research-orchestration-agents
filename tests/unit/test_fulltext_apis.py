"""Unit tests: full-text scrape adapters (offline, stubbed transports)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import ScrapeError, ScrapeSkipped
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.arxiv_fulltext import ArxivFullTextAdapter
from knowledge_engine.infrastructure.scrape.europe_pmc_fulltext import EuropePmcFullTextAdapter
from knowledge_engine.schemas.source import DocumentBody, FetchMethod

_JATS = """<?xml version="1.0"?>
<article xmlns="http://jats.nlm.nih.gov">
  <front><article-meta><title-group>
    <article-title>Test Paper on RAG</article-title>
  </title-group></article-meta></front>
  <body>
    <sec><p>First paragraph of the full text.</p><p>Second paragraph here.</p></sec>
  </body>
</article>"""


def _client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by a MockTransport handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _epmc(handler) -> EuropePmcFullTextAdapter:
    adapter = EuropePmcFullTextAdapter()
    adapter._client = lambda: _client(handler)  # type: ignore[method-assign]
    return adapter


async def test_europepmc_pmc_maps_to_rest_without_slash() -> None:
    """PMC pages map to rest/PMC{digits}/fullTextXML (slash form 404s live)."""
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text=_JATS)

    body = await _epmc(handler).to_markdown("https://europepmc.org/article/PMC/PMC13600821")
    assert seen["url"] == "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13600821/fullTextXML"
    assert body.url == "https://europepmc.org/article/PMC/PMC13600821"
    assert body.title == "Test Paper on RAG"
    assert "First paragraph" in body.markdown
    assert "Second paragraph" in body.markdown
    assert body.crawl_method is FetchMethod.API


async def test_europepmc_med_skipped_without_network() -> None:
    """MED records have no direct full-text route; the chain covers them."""
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text=_JATS)

    with pytest.raises(ScrapeSkipped):
        await _epmc(handler).to_markdown("https://europepmc.org/article/MED/42682590")
    assert called is False


async def test_europepmc_404_means_not_open_access() -> None:
    """Non-OA records (~90% of the archive) fall through to the chain."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(ScrapeError):
        await _epmc(handler).to_markdown("https://europepmc.org/article/PMC/PMC99999999")


async def test_europepmc_garbage_xml_raises() -> None:
    """Unparseable responses are typed errors, not crashes."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not xml")

    with pytest.raises(ScrapeError):
        await _epmc(handler).to_markdown("https://europepmc.org/article/PMC/PMC1")


async def test_europepmc_empty_body_raises() -> None:
    """XML without paragraphs yields nothing to synthesize from."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<article><body></body></article>")

    with pytest.raises(ScrapeError):
        await _epmc(handler).to_markdown("https://europepmc.org/article/PMC/PMC1")


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
    with pytest.raises(ScrapeSkipped):
        await ArxivFullTextAdapter(extractor=extractor).to_markdown("https://europepmc.org/article/MED/1")
    assert extractor.seen == []


async def test_arxiv_extraction_failure_propagates() -> None:
    """Extractor failures fall through to the rest of the chain."""

    class _Boom(ScrapePort):
        async def to_markdown(self, url: str) -> DocumentBody:
            raise ScrapeError("nope")

    with pytest.raises(ScrapeError):
        await ArxivFullTextAdapter(extractor=_Boom()).to_markdown("https://arxiv.org/abs/2412.11919")

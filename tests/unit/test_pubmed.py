"""Unit tests: PubMedAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.pubmed import PubMedAdapter
from knowledge_engine.schemas.source import SearchSource

_ESEARCH = {"esearchresult": {"count": "2", "retmax": "2", "idlist": ["123", "456"]}}
_ESUMMARY = {
    "result": {
        "uids": ["123", "456"],
        "123": {
            "title": "Attention and transformers",
            "authors": [{"name": "Vaswani A"}],
            "pubdate": "2017 Jun 12",
            "source": "NeurIPS",
            "articleids": [{"idtype": "doi", "value": "10.1000/xyz"}],
        },
        "456": {
            "title": "PagedAttention",
            "authors": [{"name": "Kwon W"}],
            "pubdate": "2023",
            "source": "SOSP",
            "articleids": [],
        },
    }
}


def _adapter(handler, **kwargs) -> PubMedAdapter:
    adapter = PubMedAdapter(min_interval=0, **kwargs)
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    if "esearch.fcgi" in request.url.path:
        assert request.url.params["db"] == "pubmed"
        return httpx.Response(200, json=_ESEARCH)
    assert "esummary.fcgi" in request.url.path
    return httpx.Response(200, json=_ESUMMARY)


async def test_search_maps_records_and_doi() -> None:
    records = await _adapter(_ok).search("attention", limit=10)
    assert [r.id for r in records] == ["pubmed:123", "pubmed:456"]
    assert records[1].venue == "SOSP"
    first = records[0]
    assert first.source is SearchSource.PUBMED
    assert first.title == "Attention and transformers"
    assert first.authors == ["Vaswani A"]
    assert first.year == 2017
    assert first.doi == "10.1000/xyz"
    assert records[1].doi is None


def test_parse_abstracts_reads_labeled_sections() -> None:
    from knowledge_engine.infrastructure.search.pubmed import _parse_abstracts

    xml = (
        "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>"
        "<Article><Abstract>"
        '<AbstractText Label="BACKGROUND">Why.</AbstractText>'
        "<AbstractText>What we did.</AbstractText>"
        "</Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    )
    assert _parse_abstracts(xml) == {"123": "BACKGROUND: Why.\nWhat we did."}


def test_parse_abstracts_tolerates_malformed_xml() -> None:
    from knowledge_engine.infrastructure.search.pubmed import _parse_abstracts

    assert _parse_abstracts("not xml<") == {}
    assert _parse_abstracts("<PubmedArticleSet/>") == {}


async def test_search_respects_limit() -> None:
    records = await _adapter(_ok).search("attention", limit=1)
    assert len(records) == 1


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("attention")


async def test_fetch_resolves_doi() -> None:
    record = await _adapter(_ok).fetch("10.1000/xyz")
    assert record is not None
    assert record.id == "pubmed:123"


async def _empty(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"esearchresult": {"idlist": []}})


async def test_fetch_returns_none_when_unindexed() -> None:
    assert await _adapter(_empty).fetch("10.1000/missing") is None

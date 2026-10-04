"""Unit tests: EuropePmcAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.europe_pmc import EuropePmcAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "id": "42734562",
    "source": "MED",
    "doi": "10.1021/acs.jcim.6c01049",
    "title": "Pro-GAT: Reconnecting Fragmented PROTACs",
    "authorString": "Vemuri S, Bijigiri LP",
    "pubYear": "2026",
    "abstractText": "Graph attention for PROTACs.",
    "citedByCount": 3,
    "inPMC": "N",
    "fullTextUrlList": {"fullTextUrl": [{"documentStyle": "pdf", "url": "https://example.org/p.pdf"}]},
}
_HTML_ONLY = dict(_ITEM, id="99", fullTextUrlList={"fullTextUrl": [{"documentStyle": "html", "url": "https://e.org"}]})


def _adapter(handler) -> EuropePmcAdapter:
    adapter = EuropePmcAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.params["format"] == "json"
    return httpx.Response(200, json={"resultList": {"result": [_ITEM, _HTML_ONLY]}})


async def test_search_maps_fields_and_pdf_only() -> None:
    records = await _adapter(_ok).search("pro-gat", limit=10)
    assert [r.id for r in records] == ["europe_pmc:MED:42734562", "europe_pmc:MED:99"]
    first = records[0]
    assert first.source is SearchSource.EUROPE_PMC
    assert first.doi == "10.1021/acs.jcim.6c01049"
    assert first.authors == ["Vemuri S", "Bijigiri LP"]
    assert first.year == 2026
    assert first.citation_count == 3
    assert first.pdf_url == "https://example.org/p.pdf"
    assert records[1].pdf_url is None  # html-only entry is not a PDF


async def test_search_respects_limit() -> None:
    records = await _adapter(_ok).search("pro-gat", limit=1)
    assert len(records) == 1


def test_to_record_maps_journal_venue() -> None:
    item = dict(_ITEM)
    item["journalInfo"] = {"journal": {"title": "J Chem Inf Model"}}
    assert EuropePmcAdapter._to_record(item).venue == "J Chem Inf Model"


def test_to_record_venue_missing_is_none() -> None:
    assert EuropePmcAdapter._to_record(_ITEM).venue is None


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("pro-gat")


async def test_fetch_resolves_doi() -> None:
    async def _one(request: httpx.Request) -> httpx.Response:
        assert "DOI:" in request.url.params["query"]
        return httpx.Response(200, json={"resultList": {"result": [_ITEM]}})

    record = await _adapter(_one).fetch("10.1021/acs.jcim.6c01049")
    assert record is not None
    assert record.id == "europe_pmc:MED:42734562"


async def test_fetch_returns_none_when_unindexed() -> None:
    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resultList": {"result": []}})

    assert await _adapter(_empty).fetch("10.1000/missing") is None

"""Unit tests: CrossrefAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.crossref import CrossrefAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "DOI": "10.1038/nature12373",
    "title": ["Nanometre-scale thermometry"],
    "author": [{"given": "G.", "family": "Kucsko"}],
    "issued": {"date-parts": [[2013]]},
    "abstract": "Abstract text.",
    "is-referenced-by-count": 1234,
}


def _adapter(handler) -> CrossrefAdapter:
    adapter = CrossrefAdapter("test@example.com")
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok_search(request: httpx.Request) -> httpx.Response:
    assert request.url.params["mailto"] == "test@example.com"
    return httpx.Response(200, json={"message": {"items": [_ITEM]}})


async def test_search_maps_work_fields() -> None:
    records = await _adapter(_ok_search).search("thermometry", limit=10)
    assert [r.id for r in records] == ["crossref:10.1038/nature12373"]
    first = records[0]
    assert first.source is SearchSource.CROSSREF
    assert first.doi == "10.1038/nature12373"
    assert first.title == "Nanometre-scale thermometry"
    assert first.authors == ["G. Kucsko"]
    assert first.year == 2013
    assert first.citation_count == 1234


async def _ok_fetch(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"message": _ITEM})


async def test_fetch_returns_record() -> None:
    record = await _adapter(_ok_fetch).fetch("10.1038/nature12373")
    assert record is not None
    assert record.doi == "10.1038/nature12373"


async def _missing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={})


async def test_fetch_returns_none_on_404() -> None:
    assert await _adapter(_missing).fetch("10.0000/unknown") is None


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("thermometry")

"""Unit tests: WikipediaAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.wikipedia import WikipediaAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "pageid": 12345,
    "title": "Retrieval-augmented generation",
    "snippet": 'RAG combines <span class="searchmatch">retrieval</span> with generation.',
}


def _adapter(handler) -> WikipediaAdapter:
    adapter = WikipediaAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    assert params.get("action") == "query"
    assert params.get("list") == "search"
    assert params.get("format") == "json"
    return httpx.Response(200, json={"query": {"search": [_ITEM]}})


async def test_search_maps_mediawiki_fields() -> None:
    records = await _adapter(_ok).search("retrieval augmented generation", limit=5)
    assert [r.id for r in records] == ["wikipedia:12345"]
    first = records[0]
    assert first.source is SearchSource.WIKIPEDIA
    assert first.title == "Retrieval-augmented generation"
    assert first.abstract == "RAG combines retrieval with generation."
    assert first.pdf_url is None


async def test_search_empty_results_returns_empty_list() -> None:
    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": {"search": []}})

    assert await _adapter(_empty).search("zzzznonexistent", limit=5) == []


async def test_search_raises_typed_error_on_http_failure() -> None:
    async def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    with pytest.raises(SearchError):
        await _adapter(_boom).search("rag")

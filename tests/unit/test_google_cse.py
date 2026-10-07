"""Unit tests: GoogleCSEAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.google_cse import GoogleCSEAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "title": "Retrieval-augmented generation - Wikipedia",
    "link": "https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
    "snippet": "RAG grounds LLM output in retrieved documents.",
}


def _adapter(handler) -> GoogleCSEAdapter:
    adapter = GoogleCSEAdapter(api_key="KEY", cx="CX")
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    assert params.get("key") == "KEY"
    assert params.get("cx") == "CX"
    assert params.get("q") == "retrieval augmented generation"
    return httpx.Response(200, json={"items": [_ITEM]})


async def test_search_maps_cse_items() -> None:
    records = await _adapter(_ok).search("retrieval augmented generation", limit=5)
    assert [r.id for r in records] == ["google:https://en.wikipedia.org/wiki/Retrieval-augmented_generation"]
    first = records[0]
    assert first.source is SearchSource.GOOGLE
    assert first.title == "Retrieval-augmented generation - Wikipedia"
    assert first.abstract == "RAG grounds LLM output in retrieved documents."
    assert first.pdf_url is None


async def test_search_no_items_returns_empty_list() -> None:
    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert await _adapter(_empty).search("zzzznonexistent", limit=5) == []


async def test_search_raises_typed_error_on_http_failure() -> None:
    async def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota exceeded"}})

    with pytest.raises(SearchError):
        await _adapter(_boom).search("rag")

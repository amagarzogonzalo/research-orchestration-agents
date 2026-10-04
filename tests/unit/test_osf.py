"""Unit tests: OSFPreprintsAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.osf import OSFPreprintsAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "id": "abc12",
    "type": "preprints",
    "attributes": {
        "title": "Social-RAG for Telegram",
        "doi": "10.31219/osf.io/abc12",
        "description": "Abstract text.",
        "date_published": "2024-05-01T00:00:00Z",
    },
}


def _adapter(handler) -> OSFPreprintsAdapter:
    adapter = OSFPreprintsAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.params.get("filter[title]") == "social rag"
    return httpx.Response(200, json={"data": [_ITEM]})


async def test_search_maps_jsonapi_fields() -> None:
    records = await _adapter(_ok).search("social rag", limit=5)
    assert [r.id for r in records] == ["osf:abc12"]
    first = records[0]
    assert first.source is SearchSource.OSF_PREPRINTS
    assert first.doi == "10.31219/osf.io/abc12"
    assert first.title == "Social-RAG for Telegram"
    assert first.abstract == "Abstract text."
    assert first.year == 2024


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"errors": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("x")


def _item(id: str, title: str) -> dict:
    return {"id": id, "attributes": {"title": title, "doi": None, "description": None, "date_published": None}}


async def test_search_falls_back_to_keywords_on_empty_phrase() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        title = request.url.params.get("filter[title]", "")
        calls.append(title)
        if " " in title:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [_item("w1", "t")]})

    records = await _adapter(handler).search("How to scale agents", limit=5)
    assert [r.id for r in records] == ["osf:w1"]
    assert calls[0] == "How to scale agents"
    assert len(calls) > 1


async def test_search_dedupes_keyword_hits() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        title = str(request.url.params.get("filter[title]", ""))
        if " " in title:
            return httpx.Response(200, json={"data": [_item("w1", "t")]})
        return httpx.Response(200, json={"data": [_item("w1", "t"), _item("w1", "t2")]})

    records = await _adapter(handler).search("scaling agents cloud", limit=5)
    assert [r.id for r in records] == ["osf:w1"]

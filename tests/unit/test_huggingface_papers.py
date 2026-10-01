"""Unit tests: HuggingFacePapersAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.huggingface_papers import HuggingFacePapersAdapter
from knowledge_engine.schemas.source import SearchSource

_PAYLOAD = [
    {
        "paper": {
            "id": "1706.03762",
            "title": "Attention Is All You Need",
            "summary": "The Transformer...",
            "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
            "publishedAt": "2017-06-12T13:57:34.000Z",
            "upvotes": 120,
        },
        "upvoted": False,
        "numComments": 14,
        "publishedAt": "2017-06-12T13:57:34.000Z",
    },
    {
        "paper": {
            "id": "2309.06180",
            "title": "Efficient Memory Management for LLM Serving with PagedAttention",
            "summary": "PagedAttention...",
            "authors": [{"name": "Woosuk Kwon"}],
            "publishedAt": "2023-09-12T00:00:00.000Z",
            "upvotes": 75,
        },
        "upvoted": False,
        "numComments": 3,
        "publishedAt": "2023-09-12T00:00:00.000Z",
    },
]


def _adapter(handler) -> HuggingFacePapersAdapter:
    adapter = HuggingFacePapersAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.params["q"] == "attention"
    return httpx.Response(200, json=_PAYLOAD)


async def test_search_ranks_by_upvotes_and_maps_fields() -> None:
    records = await _adapter(_ok).search("attention", limit=10)
    assert [r.id for r in records] == ["huggingface_papers:1706.03762", "huggingface_papers:2309.06180"]
    first = records[0]
    assert first.title == "Attention Is All You Need"
    assert first.source is SearchSource.HUGGINGFACE_PAPERS
    assert first.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.year == 2017
    assert first.abstract == "The Transformer..."
    assert first.pdf_url == "https://arxiv.org/pdf/1706.03762"


async def test_search_respects_limit() -> None:
    records = await _adapter(_ok).search("attention", limit=1)
    assert len(records) == 1


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("attention")

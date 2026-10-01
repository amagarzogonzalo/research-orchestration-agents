"""Unit tests: DblpAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.dblp import DblpAdapter
from knowledge_engine.schemas.source import SearchSource

_HITS = {
    "result": {
        "hits": {
            "hit": [
                {
                    "@key": "conf/icml/SuzgunBGSS19",
                    "info": {
                        "title": "Memory-augmented recurrent networks",
                        "authors": {"author": ["Mirac Suzgun", "Sebastian Gehrmann"]},
                        "year": "2019",
                        "doi": "10.1000/dblp1",
                        "url": "https://dblp.org/rec/conf/icml/SuzgunBGSS19",
                        "ee": "https://arxiv.org/pdf/1911.03329",
                    },
                },
                {
                    "@key": "journals/corr/Solo19",
                    "info": {
                        "title": "Solo author note",
                        "authors": {"author": "Jane Solo"},
                        "year": "2020",
                        "ee": "https://example.org/landing",
                    },
                },
            ]
        }
    }
}


def _adapter(handler) -> DblpAdapter:
    adapter = DblpAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.params["format"] == "json"
    return httpx.Response(200, json=_HITS)


async def test_search_maps_fields_and_pdf_only() -> None:
    records = await _adapter(_ok).search("memory networks", limit=10)
    assert [r.id for r in records] == ["dblp:conf/icml/SuzgunBGSS19", "dblp:journals/corr/Solo19"]
    first = records[0]
    assert first.source is SearchSource.DBLP
    assert first.authors == ["Mirac Suzgun", "Sebastian Gehrmann"]
    assert first.year == 2019
    assert first.doi == "10.1000/dblp1"
    assert first.pdf_url == "https://arxiv.org/pdf/1911.03329"
    assert records[1].authors == ["Jane Solo"]  # single-author string form
    assert records[1].pdf_url is None  # landing page is not a PDF


async def test_search_respects_limit() -> None:
    records = await _adapter(_ok).search("memory networks", limit=1)
    assert len(records) == 1


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("memory networks")

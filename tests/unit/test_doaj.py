"""Unit tests: DoajAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.doaj import DoajAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "id": "000467184ca2468f921d08c2074a7fab",
    "bibjson": {
        "title": "Small Object Detection with YOLOv8",
        "author": [{"name": "Hao Yi"}, {"name": "Bo Liu"}],
        "year": "2024",
        "identifier": [{"id": "2151-1535", "type": "eissn"}, {"id": "10.1109/JSTARS.2023.3339235", "type": "doi"}],
        "link": [{"type": "fulltext", "url": "https://ieeexplore.ieee.org/document/10342786/"}],
        "abstract": "Improved YOLOv8 for remote sensing.",
    },
}


def _adapter(handler) -> DoajAdapter:
    adapter = DoajAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [_ITEM]})


async def test_search_maps_bibjson_fields() -> None:
    records = await _adapter(_ok).search("yolo remote sensing", limit=10)
    assert [r.id for r in records] == ["doaj:000467184ca2468f921d08c2074a7fab"]
    first = records[0]
    assert first.source is SearchSource.DOAJ
    assert first.title == "Small Object Detection with YOLOv8"
    assert first.authors == ["Hao Yi", "Bo Liu"]
    assert first.year == 2024
    assert first.doi == "10.1109/JSTARS.2023.3339235"
    assert first.pdf_url == "https://ieeexplore.ieee.org/document/10342786/"


async def test_search_respects_limit() -> None:
    records = await _adapter(_ok).search("yolo", limit=0)
    assert records == []


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("yolo")

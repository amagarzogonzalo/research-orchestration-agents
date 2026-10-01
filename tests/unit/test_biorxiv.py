"""Unit tests: BiorxivAdapter for both servers (offline, stubbed transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.biorxiv import BiorxivAdapter
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "doi": "10.1101/2024.10.30.621034",
    "title": "Single neurons detect transitions",
    "authors": "Asopa, A.; Bhalla, U. S.",
    "date": "2026-09-01",
    "version": "4",
    "category": "neuroscience",
    "abstract": "Mismatch detection in hippocampus.",
    "server": "bioRxiv",
}
_OTHER = dict(_ITEM, title="Unrelated kidney study", abstract="Renal filtration markers.")


def _adapter(handler, server="biorxiv") -> BiorxivAdapter:
    adapter = BiorxivAdapter(server)
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _browse(request: httpx.Request) -> httpx.Response:
    assert "/details/biorxiv/" in request.url.path
    return httpx.Response(200, json={"collection": [_ITEM, _OTHER]})


async def test_search_filters_window_by_query_tokens() -> None:
    records = await _adapter(_browse).search("hippocampus mismatch")
    assert len(records) == 1
    rec = records[0]
    assert rec.id == "biorxiv:10.1101/2024.10.30.621034"
    assert rec.source is SearchSource.BIORXIV
    assert rec.authors == ["Asopa, A.", "Bhalla, U. S."]
    assert rec.year == 2026
    assert rec.doi == "10.1101/2024.10.30.621034"


async def test_medrxiv_server_maps_own_source() -> None:
    async def _med(request: httpx.Request) -> httpx.Response:
        assert "/details/medrxiv/" in request.url.path
        return httpx.Response(200, json={"collection": [_ITEM]})

    records = await _adapter(_med, server="medrxiv").search("hippocampus")
    assert records[0].source is SearchSource.MEDRXIV
    assert records[0].id.startswith("medrxiv:")


async def _boom(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "down"})


async def test_search_raises_typed_error_on_http_failure() -> None:
    with pytest.raises(SearchError):
        await _adapter(_boom).search("hippocampus")


async def test_fetch_resolves_doi() -> None:
    async def _one(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"collection": [_ITEM]})

    record = await _adapter(_one).fetch("10.1101/2024.10.30.621034")
    assert record is not None
    assert record.title == "Single neurons detect transitions"


async def test_fetch_returns_none_when_empty() -> None:
    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"collection": []})

    assert await _adapter(_empty).fetch("10.1101/missing") is None

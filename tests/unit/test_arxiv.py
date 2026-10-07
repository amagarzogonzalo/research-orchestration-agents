"""Offline tests for the arXiv adapter (no network; MockTransport).

Live probe 2026-10-03: ``export.arxiv.org/api/query`` throttles fan-out
traffic (HTTP 429; occasionally slow enough to read-timeout). The adapter
answers a 429 with one polite retry after 5s, per the API's stated etiquette.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.arxiv import ArxivAdapter
from knowledge_engine.schemas.source import SearchSource

_FEED = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<id>http://arxiv.org/abs/2301.00001v1</id>
<title>Synthetic data for pretraining</title>
<summary>A short abstract.</summary>
<published>2023-01-15T00:00:00Z</published>
<author><name>Jane Doe</name></author>
<link rel="related" title="pdf" href="https://arxiv.org/pdf/2301.00001"/>
</entry>
</feed>"""


def _adapter(handler) -> ArxivAdapter:
    """Build an adapter backed by a MockTransport handler."""
    adapter = ArxivAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    return adapter


async def test_search_parses_atom_feed() -> None:
    """Parse entries from a stubbed Atom response."""
    records = await _adapter(lambda request: httpx.Response(200, text=_FEED)).search("pretraining")
    assert len(records) == 1
    rec = records[0]
    assert rec.id == "arxiv:2301.00001v1"
    assert rec.source is SearchSource.ARXIV
    assert rec.title == "Synthetic data for pretraining"
    assert rec.authors == ["Jane Doe"]
    assert rec.year == 2023
    assert rec.pdf_url == "https://arxiv.org/pdf/2301.00001"


async def test_search_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back off and retry a single time when throttled, then succeed."""
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    async def _flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, text=_FEED)

    records = await _adapter(_flaky).search("pretraining")
    assert len(records) == 1
    assert calls["n"] == 2
    assert sleeps == [5]


async def test_search_raises_typed_error() -> None:
    """Persistent failures surface as SearchError."""
    with pytest.raises(SearchError):
        await _adapter(lambda request: httpx.Response(500, text="down")).search("pretraining")

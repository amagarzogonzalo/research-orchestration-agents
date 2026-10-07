"""Unit tests: ExaAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.infrastructure.search.exa import ExaAdapter
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.schemas.source import SearchSource

_BODY = {
    "requestId": "r1",
    "results": [
        {
            "title": "RAG paper",
            "url": "https://ex.io/paper",
            "author": "A. Author",
            "publishedDate": "2023-05-01",
            "highlights": ["snippet one", "snippet two"],
        },
        {"title": "No date", "url": "https://ex.io/nd", "highlights": []},
    ],
}


def _ledger(tmp_path) -> MonthlyQuotaLedger:
    return MonthlyQuotaLedger("exa", limit=100, root=tmp_path, clock=lambda: datetime(2026, 10, 1, tzinfo=UTC))


def _adapter(monkeypatch, handler, ledger=None) -> ExaAdapter:
    """Build an adapter whose AsyncClient uses a MockTransport (headers kept)."""
    adapter = ExaAdapter("exa-test", ledger=ledger)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return adapter


async def test_parses_results(monkeypatch) -> None:
    records = await _adapter(monkeypatch, lambda r: httpx.Response(200, json=_BODY)).search("rag", limit=5)
    first = records[0]
    assert first.id == "exa:https://ex.io/paper"
    assert first.source is SearchSource.EXA
    assert first.abstract == "snippet one"
    assert first.year == 2023
    assert first.authors == ["A. Author"]
    assert records[1].year is None


async def test_sends_api_key_header_and_instant_mode(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key", "")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=_BODY)

    await _adapter(monkeypatch, handler).search("rag", limit=4)
    assert seen["key"] == "exa-test"
    assert '"type":"instant"' in seen["body"]
    assert '"numResults":4' in seen["body"]


async def test_payment_required_marks_exhausted(monkeypatch, tmp_path) -> None:
    ledger = _ledger(tmp_path)
    adapter = _adapter(monkeypatch, lambda r: httpx.Response(402, json={"error": "out of credits"}), ledger)
    with pytest.raises(QuotaExhausted):
        await adapter.search("rag")
    assert await ledger.available() is False


async def test_exhausted_ledger_skips_network(monkeypatch, tmp_path) -> None:
    ledger = _ledger(tmp_path)
    await ledger.record(exhausted=True)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_BODY)

    with pytest.raises(QuotaExhausted):
        await _adapter(monkeypatch, handler, ledger).search("rag")
    assert calls["n"] == 0


async def test_server_error_raises_search_error(monkeypatch) -> None:
    with pytest.raises(SearchError):
        await _adapter(monkeypatch, lambda r: httpx.Response(503, json={})).search("rag")

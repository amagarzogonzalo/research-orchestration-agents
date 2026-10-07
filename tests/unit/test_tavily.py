"""Unit tests: TavilyAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.infrastructure.search.tavily import TavilyAdapter
from knowledge_engine.schemas.source import SearchSource

_BODY = {
    "query": "rag",
    "results": [
        {"title": "RAG", "url": "https://ex.io/rag", "content": "retrieval", "score": 0.9},
        {"title": "Second", "url": "https://ex.io/2", "content": "more", "score": 0.5},
    ],
}


def _ledger(tmp_path) -> MonthlyQuotaLedger:
    return MonthlyQuotaLedger("tavily", limit=100, root=tmp_path, clock=lambda: datetime(2026, 10, 1, tzinfo=UTC))


def _adapter(monkeypatch, handler, ledger=None) -> TavilyAdapter:
    """Build an adapter whose AsyncClient uses a MockTransport (headers kept)."""
    adapter = TavilyAdapter("tvly-test", ledger=ledger)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return adapter


async def test_parses_results(monkeypatch) -> None:
    records = await _adapter(monkeypatch, lambda r: httpx.Response(200, json=_BODY)).search("rag", limit=5)
    assert records[0].id == "tavily:https://ex.io/rag"
    assert records[0].source is SearchSource.TAVILY
    assert records[0].abstract == "retrieval"
    assert len(records) == 2


async def test_sends_bearer_auth_and_body(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=_BODY)

    await _adapter(monkeypatch, handler).search("rag", limit=3)
    assert seen["auth"] == "Bearer tvly-test"
    assert '"max_results":3' in seen["body"]
    assert '"search_depth":"basic"' in seen["body"]


async def test_quota_status_marks_exhausted(monkeypatch, tmp_path) -> None:
    ledger = _ledger(tmp_path)
    adapter = _adapter(monkeypatch, lambda r: httpx.Response(432, json={"detail": {"error": "limit"}}), ledger)
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
        await _adapter(monkeypatch, lambda r: httpx.Response(500, json={})).search("rag")


async def test_transport_error_raises_search_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(SearchError):
        await _adapter(monkeypatch, handler).search("rag")

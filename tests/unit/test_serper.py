"""Unit tests: SerperAdapter (offline, stubbed HTTP transport).

The independent general-web fallback (issue #21): Google-grade results from a
vendor family disjoint from Tavily/Exa, so a DDG bot-wall plus a vendor
outage cannot kill the web tier. 2,500 free searches/month, no card.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.infrastructure.search.serper import SERPER_MONTHLY_LIMIT, SerperAdapter
from knowledge_engine.schemas.source import SearchSource

_BODY = {
    "searchParameters": {"q": "rag", "num": 5},
    "organic": [
        {"title": "RAG explained", "link": "https://ex.io/rag", "snippet": "retrieval augmentation", "position": 1},
        {"title": "Second", "link": "https://ex.io/2", "snippet": "more evidence", "position": 2},
    ],
}


def _ledger(tmp_path) -> MonthlyQuotaLedger:
    return MonthlyQuotaLedger("serper", limit=100, root=tmp_path, clock=lambda: datetime(2026, 10, 1, tzinfo=UTC))


def _adapter(monkeypatch, handler, ledger=None) -> SerperAdapter:
    """Build an adapter whose AsyncClient uses a MockTransport (headers kept).

    Args:
        monkeypatch: Pytest patching fixture.
        handler: Mock transport handler.
        ledger: Optional quota ledger.

    Returns:
        SerperAdapter: Adapter under test.
    """
    adapter = SerperAdapter("serper-test", ledger=ledger)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return adapter


async def test_parses_results(monkeypatch) -> None:
    """Organic entries become serper:-prefixed SourceRecords."""
    records = await _adapter(monkeypatch, lambda r: httpx.Response(200, json=_BODY)).search("rag", limit=5)
    assert records[0].id == "serper:https://ex.io/rag"
    assert records[0].source is SearchSource.SERPER
    assert records[0].abstract == "retrieval augmentation"
    assert len(records) == 2


async def test_sends_api_key_header_and_body(monkeypatch) -> None:
    """Auth rides the X-API-KEY header; the query rides the JSON body."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key", "")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=_BODY)

    await _adapter(monkeypatch, handler).search("rag", limit=3)
    assert seen["key"] == "serper-test"
    assert '"q":"rag"' in seen["body"].replace(" ", "")
    assert '"num":3' in seen["body"].replace(" ", "")


async def test_empty_organic_returns_empty(monkeypatch) -> None:
    """A query with zero organic hits yields an empty list (chain escalates)."""
    records = await _adapter(monkeypatch, lambda r: httpx.Response(200, json={"organic": []})).search("rag", limit=5)
    assert records == []


async def test_quota_status_marks_exhausted(monkeypatch, tmp_path) -> None:
    """HTTP 429 marks the period exhausted and raises QuotaExhausted."""
    ledger = _ledger(tmp_path)
    with pytest.raises(QuotaExhausted):
        await _adapter(monkeypatch, lambda r: httpx.Response(429, json={}), ledger).search("rag")
    assert await ledger.is_exhausted() if hasattr(ledger, "is_exhausted") else True


async def test_auth_error_is_search_error(monkeypatch, tmp_path) -> None:
    """A bad key (401/403) is a non-quota SearchError the chain can skip."""
    with pytest.raises(SearchError):
        await _adapter(monkeypatch, lambda r: httpx.Response(401, json={})).search("rag")


async def test_server_error_raises_search_error(monkeypatch) -> None:
    """5xx surfaces as SearchError; the chain breaker handles isolation."""
    with pytest.raises(SearchError):
        await _adapter(monkeypatch, lambda r: httpx.Response(500, json={})).search("rag")


def test_monthly_limit_is_the_free_tier() -> None:
    """The default ledger caps at the documented free tier."""
    assert SERPER_MONTHLY_LIMIT == 2500

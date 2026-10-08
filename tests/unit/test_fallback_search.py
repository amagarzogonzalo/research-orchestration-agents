"""Unit tests: FallbackSearchAdapter (offline fakes, no network)."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.infrastructure.search.fallback import FallbackSearchAdapter
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _rec(name: str) -> SourceRecord:
    return SourceRecord(id=f"{name}:1", title=name, source=SearchSource.DUCKDUCKGO)


class _Provider(SearchPort):
    """Provider that returns canned results or raises a canned error."""

    def __init__(
        self, name: str, *, results: list[SourceRecord] | None = None, error: Exception | None = None
    ) -> None:
        self.name = name
        self._results = results if results is not None else [_rec(name)]
        self._error = error
        self.calls = 0

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._results


async def test_first_success_wins() -> None:
    a, b = _Provider("a"), _Provider("b")
    got = await FallbackSearchAdapter([a, b]).search("q")
    assert got[0].id == "a:1"
    assert b.calls == 0


async def test_error_then_next() -> None:
    a = _Provider("a", error=SearchError("boom"))
    b = _Provider("b")
    got = await FallbackSearchAdapter([a, b]).search("q")
    assert got[0].id == "b:1"


async def test_empty_then_next() -> None:
    a = _Provider("a", results=[])
    b = _Provider("b")
    got = await FallbackSearchAdapter([a, b]).search("q")
    assert got[0].id == "b:1"


async def test_quota_exhausted_is_skipped() -> None:
    a = _Provider("a", error=QuotaExhausted("done"))
    b = _Provider("b")
    got = await FallbackSearchAdapter([a, b]).search("q")
    assert got[0].id == "b:1"


async def test_all_fail_returns_empty() -> None:
    a = _Provider("a", error=SearchError("x"))
    b = _Provider("b", error=SearchError("y"))
    assert await FallbackSearchAdapter([a, b]).search("q") == []


async def test_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        FallbackSearchAdapter([])


async def test_repeated_failures_open_breaker() -> None:
    a = _Provider("a", error=SearchError("down"))
    adapter = FallbackSearchAdapter([a])
    for _ in range(3):
        assert await adapter.search("q") == []
    assert a.calls == 3
    assert await adapter.search("q") == []
    assert a.calls == 3  # breaker open: not called again


async def test_injected_breaker_is_honoured() -> None:
    class _Open:
        async def allow(self) -> bool:
            return False

        async def record_success(self) -> None:
            return None

        async def record_failure(self) -> None:
            return None

    a = _Provider("a")
    adapter = FallbackSearchAdapter([a], breakers={"a": _Open()})
    assert await adapter.search("q") == []
    assert a.calls == 0


async def test_name_is_general_web() -> None:
    assert FallbackSearchAdapter([_Provider("a")]).name == "general_web"

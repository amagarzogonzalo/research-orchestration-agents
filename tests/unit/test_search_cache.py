"""Unit tests: SearchCache + CachedSearchAdapter (offline, tmp files)."""

from __future__ import annotations

import os
import time

import pytest

from knowledge_engine.core.ports import SearchPort
from knowledge_engine.infrastructure.search.cache import CachedSearchAdapter, SearchCache
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _record(i: int) -> SourceRecord:
    return SourceRecord(id=f"x:{i}", title=f"title {i}", source=SearchSource.TAVILY)


class _Inner(SearchPort):
    """Counting fake search adapter."""

    name = "inner"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        self.calls += 1
        return [
            SourceRecord(id=f"inner:{i}", title=f"{query} {i}", source=SearchSource.OPENALEX) for i in range(limit)
        ]


async def test_set_get_roundtrip(tmp_path) -> None:
    cache = SearchCache(tmp_path)
    assert await cache.get("q") is None
    await cache.set("q", [_record(0), _record(1)])
    got = await cache.get("q")
    assert got is not None
    assert [r.id for r in got] == ["x:0", "x:1"]


async def test_normalization_shares_entry(tmp_path) -> None:
    cache = SearchCache(tmp_path)
    await cache.set("Retrieval   Augmented, Generation!", [_record(0)])
    assert await cache.get("retrieval augmented generation") is not None


async def test_empty_not_cached(tmp_path) -> None:
    cache = SearchCache(tmp_path)
    await cache.set("q", [])
    assert await cache.get("q") is None


async def test_ttl_expiry(tmp_path) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10)
    await cache.set("q", [_record(0)])
    old = time.time() - 100
    os.utime(cache.path_for("q"), (old, old))
    assert await cache.get("q") is None


async def test_corrupt_entry_is_a_miss(tmp_path) -> None:
    cache = SearchCache(tmp_path)
    path = cache.path_for("q")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert await cache.get("q") is None


async def test_cached_adapter_avoids_second_call(tmp_path) -> None:
    inner = _Inner()
    adapter = CachedSearchAdapter(inner, SearchCache(tmp_path))
    first = await adapter.search("q", limit=3)
    second = await adapter.search("q", limit=3)
    assert inner.calls == 1
    assert [r.id for r in first] == [r.id for r in second]


async def test_cached_adapter_slices_limit(tmp_path) -> None:
    inner = _Inner()
    adapter = CachedSearchAdapter(inner, SearchCache(tmp_path))
    await adapter.search("q", limit=5)
    got = await adapter.search("q", limit=2)
    assert len(got) == 2


async def test_cached_adapter_name_is_prefixed(tmp_path) -> None:
    adapter = CachedSearchAdapter(_Inner(), SearchCache(tmp_path))
    assert adapter.name == "cached:inner"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])

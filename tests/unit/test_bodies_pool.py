"""Unit tests: bounded scrape pool with general-web reserve (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.bodies import (
    _MAX_GENERAL_BODIES,
    _MAX_SCHOLARLY_BODIES,
    select_scrape_pool,
)
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(sid: str, source: SearchSource = SearchSource.ARXIV) -> SourceRecord:
    return SourceRecord(id=sid, title=f"Paper {sid}", source=source)


def _wiki(sid: str) -> SourceRecord:
    return _src(sid, SearchSource.WIKIPEDIA)


def _ddg(sid: str) -> SourceRecord:
    return _src(sid, SearchSource.DUCKDUCKGO)


def test_small_pool_passes_through_unchanged() -> None:
    """Below both caps, order and content are untouched."""
    pool = [_src("a:1"), _wiki("w:1"), _src("a:2")]
    assert [s.id for s in select_scrape_pool(pool)] == ["a:1", "w:1", "a:2"]


def test_scholarly_cap_preserves_order() -> None:
    """Overflow drops from the tail (lowest source priority), never reorders."""
    pool = [_src(f"a:{i}") for i in range(_MAX_SCHOLARLY_BODIES + 5)]
    chosen = select_scrape_pool(pool)
    assert len(chosen) == _MAX_SCHOLARLY_BODIES
    assert [s.id for s in chosen] == [f"a:{i}" for i in range(_MAX_SCHOLARLY_BODIES)]


def test_general_web_reserve_survives_scholarly_flood() -> None:
    """General-web records keep their quota no matter the scholarly volume."""
    pool = [_src(f"a:{i}") for i in range(40)] + [_wiki("w:1"), _ddg("d:1")]
    chosen = select_scrape_pool(pool)
    assert len(chosen) == _MAX_SCHOLARLY_BODIES + 2
    assert chosen[-2].id == "w:1"
    assert chosen[-1].id == "d:1"


def test_general_cap_bounds() -> None:
    """The web reserve itself is bounded."""
    pool = [_wiki(f"w:{i}") for i in range(_MAX_GENERAL_BODIES + 3)]
    assert len(select_scrape_pool(pool)) == _MAX_GENERAL_BODIES


def test_empty_pool() -> None:
    """No sources means no sends (join handles the empty case)."""
    assert select_scrape_pool([]) == []


def test_dict_snapshots_supported() -> None:
    """Post-resume dict snapshots classify by their source string."""
    pool = [{"id": "w:1", "source": "wikipedia"}, {"id": "a:1", "source": "arxiv"}]
    chosen = select_scrape_pool(pool)
    assert [s["id"] for s in chosen] == ["w:1", "a:1"]


def test_unknown_source_counts_as_scholarly() -> None:
    """Unrecognized source strings fall back to the scholarly bucket."""
    pool = [{"id": "x:1", "source": "something-new"}]
    assert [s["id"] for s in select_scrape_pool(pool)] == ["x:1"]

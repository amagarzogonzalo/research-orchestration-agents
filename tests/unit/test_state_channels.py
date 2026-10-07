"""Unit tests: branch_sources channel + merge_sources (deep provenance)."""

from __future__ import annotations

from knowledge_engine.orchestration.state import merge_sources
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(id: str) -> SourceRecord:
    return SourceRecord(id=id, title="t", source=SearchSource.ARXIV)


def test_merge_sources_dedupes_by_id_preserving_order() -> None:
    a, b, c = _src("openalex:W1"), _src("openalex:W2"), _src("openalex:W3")
    assert merge_sources([a, b], [b, c]) == [a, b, c]


def test_merge_sources_empty_inputs() -> None:
    assert merge_sources([], []) == []
    assert merge_sources([_src("openalex:W1")]) == [_src("openalex:W1")]


def test_branch_sources_key_absent_defaults_empty() -> None:
    state: dict = {"sources": [_src("openalex:W1")]}
    merged = merge_sources(state.get("sources", []), state.get("branch_sources", []))
    assert [s.id for s in merged] == ["openalex:W1"]

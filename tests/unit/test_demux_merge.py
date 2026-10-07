"""Unit tests: demux merge dedupes by DOI, id, then normalized title."""

from __future__ import annotations

from knowledge_engine.infrastructure.search.demux import _merge, _norm_title
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _rec(id: str, title: str, doi: str | None = None) -> SourceRecord:
    return SourceRecord(id=id, title=title, source=SearchSource.OPENALEX, doi=doi)


def test_merge_dedupes_by_doi() -> None:
    a = _rec("openalex:W1", "Alpha", doi="10.1/x")
    b = _rec("crossref:10.1/x", "Alpha Different Title", doi="10.1/x")
    assert _merge([[a], [b]]) == [a]


def test_merge_dedupes_same_title_different_doi() -> None:
    title = "Refining AI Vision: How Retrieval-Augmented Generation Transforms Image Captioning"
    a = _rec("crossref:10.1/a", title, doi="10.1/a")
    b = _rec("crossref:10.1/b", title, doi="10.1/b")
    merged = _merge([[a], [b]])
    assert [r.id for r in merged] == ["crossref:10.1/a"]


def test_merge_keeps_short_generic_titles() -> None:
    a = _rec("openalex:W1", "Introduction", doi="10.1/a")
    b = _rec("openalex:W2", "Introduction", doi="10.1/b")
    assert len(_merge([[a], [b]])) == 2


def test_merge_preserves_first_seen_order() -> None:
    a = _rec("openalex:W1", "Alpha paper on retrieval", doi="10.1/a")
    b = _rec("openalex:W2", "Beta paper on reranking methods", doi="10.1/b")
    assert [r.id for r in _merge([[b], [a]])] == ["openalex:W2", "openalex:W1"]


def test_norm_title_ignores_case_and_punctuation() -> None:
    assert _norm_title("Hello, World!") == _norm_title("hello world")

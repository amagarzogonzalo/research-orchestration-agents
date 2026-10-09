"""Unit tests: SourceCleaner tiered dedup (golden pairs + close-never-delete)."""

from __future__ import annotations

from knowledge_engine.infrastructure.clean.cleaner import SourceCleaner
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _rec(rid: str, title: str, abstract: str | None = None, doi: str | None = None) -> SourceRecord:
    """Build a minimal SourceRecord.

    Args:
        rid: Canonical id.
        title: Title.
        abstract: Optional abstract.
        doi: Optional DOI.

    Returns:
        SourceRecord: The record.
    """
    return SourceRecord(id=rid, title=title, source=SearchSource.OPENALEX, abstract=abstract, doi=doi)


async def test_exact_id_duplicate_dropped() -> None:
    """Same canonical id twice: second dropped, pointer to keeper."""
    cleaner = SourceCleaner()
    result = await cleaner.clean([_rec("a:1", "Alpha"), _rec("a:1", "Alpha again")])
    assert result.exact_drops == 1
    assert result.superseded_by == {"a:1": "a:1"}
    assert [r.id for r in result.kept] == ["a:1"]


async def test_same_doi_kept_by_default_dropped_when_flag_on() -> None:
    """DOI dedup is opt-in in Loop 0 (eval replay safety)."""
    cleaner = SourceCleaner()
    relaxed = await cleaner.clean([_rec("a:1", "Alpha", doi="10.1/x"), _rec("b:2", "Alpha copy", doi="10.1/x")])
    assert len(relaxed.kept) == 2

    strict = SourceCleaner(dedup_doi=True)
    result = await strict.clean([_rec("a:1", "Alpha", doi="10.1/x"), _rec("b:2", "Alpha copy", doi="10.1/x")])
    assert result.exact_drops == 1
    assert result.superseded_by == {"b:2": "a:1"}


async def test_distinct_sources_kept() -> None:
    """Unrelated records all survive; nothing dropped, nothing written."""
    cleaner = SourceCleaner()
    result = await cleaner.clean(
        [
            _rec("a:1", "Quantum error correction", "surface codes"),
            _rec("b:2", "Roman aqueduct engineering", "hydraulics"),
        ]
    )
    assert result.dropped == 0
    assert len(result.kept) == 2


async def test_near_duplicate_paraphrase_merged() -> None:
    """A paraphrased near-dup is caught by the MinHash pass and clustered."""
    cleaner = SourceCleaner(near_dedup=True)
    abstract = "retrieval augmented generation grounds answers in retrieved documents and reduces hallucination"
    result = await cleaner.clean(
        [
            _rec("a:1", "RAG reduces hallucinations in large language models", abstract),
            _rec("b:2", "RAG reduces hallucinations in large language models", abstract),
        ]
    )
    assert result.near_drops == 1
    assert result.clusters == 1
    assert result.superseded_by == {"b:2": "a:1"}
    assert [r.id for r in result.kept] == ["a:1"]


async def test_containment_longer_supersedes_shorter() -> None:
    """A contained short record is superseded by a much longer one."""
    cleaner = SourceCleaner(near_dedup=True)
    short = "attention mechanisms power modern transformers"
    long_text = (
        "attention mechanisms power modern transformers and every follow-up variant "
        "multi head attention sparse attention linear attention rotary embeddings all "
        "build on the same core primitive of learned query key value mixing"
    )
    result = await cleaner.clean([_rec("s:1", short, short), _rec("l:1", long_text[:80], long_text)])
    assert result.containment_drops + result.near_drops >= 1
    assert "s:1" in result.superseded_by or "l:1" in result.superseded_by


async def test_close_never_delete_superseded_kept_in_map() -> None:
    """Dropped ids live in superseded_by (history retrievable), nothing deleted."""
    cleaner = SourceCleaner()
    result = await cleaner.clean([_rec("a:1", "X"), _rec("a:1", "X"), _rec("a:1", "X")])
    assert result.dropped == 2
    assert set(result.superseded_by) == {"a:1"}
    assert [r.id for r in result.kept] == ["a:1"]

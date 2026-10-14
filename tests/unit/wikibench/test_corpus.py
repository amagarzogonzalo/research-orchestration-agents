"""Unit tests: WikiBench fictional corpus generation + vault seeding."""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.scripts.wikibench import corpus
from knowledge_engine.scripts.wikibench.schemas import QueryKind


def test_build_corpus_is_deterministic() -> None:
    """Same (seed, n_pages) yields identical pages and queries."""
    a = corpus.build_corpus(25, seed=11)
    b = corpus.build_corpus(25, seed=11)
    assert a.pages == b.pages
    assert a.queries == b.queries


def test_build_corpus_seed_changes_content() -> None:
    """A different seed yields different content (not merely reordered)."""
    a = corpus.build_corpus(25, seed=1)
    b = corpus.build_corpus(25, seed=2)
    assert [p.slug for p in a.pages] == [p.slug for p in b.pages]
    assert [p.marker for p in a.pages] != [p.marker for p in b.pages]


def test_corpus_counts_and_unique_slugs() -> None:
    """Page count matches the request and slugs are unique."""
    built = corpus.build_corpus(30)
    assert len(built.pages) == 30
    slugs = [p.slug for p in built.pages]
    assert len(set(slugs)) == 30


def test_all_query_kinds_present() -> None:
    """Every query kind is generated."""
    kinds = {q.kind for q in corpus.build_corpus(20).queries}
    assert kinds == set(QueryKind)


def test_gold_slugs_resolve_to_pages() -> None:
    """Every gold slug references a real seeded page."""
    built = corpus.build_corpus(20)
    known = {p.slug for p in built.pages}
    for query in built.queries:
        assert set(query.gold_slugs) <= known


def test_unanswerable_queries_have_no_gold() -> None:
    """Unanswerable cases carry an empty gold list."""
    unanswerable = [q for q in corpus.build_corpus(20).queries if q.kind is QueryKind.UNANSWERABLE]
    assert unanswerable
    assert all(q.gold_slugs == [] for q in unanswerable)


def test_seed_vault_writes_pages_and_index(tmp_path: Path) -> None:
    """Seeding writes one markdown page per corpus page plus an index."""
    built = corpus.build_corpus(12)
    corpus.seed_vault(tmp_path, built)
    pages = list((tmp_path / "sources").glob("*.md"))
    assert len(pages) == 12
    assert (tmp_path / "index.md").is_file()


def test_seed_vault_sidecar_is_built(tmp_path: Path) -> None:
    """sidecar=True builds the FTS5 index beside the vault."""
    corpus.seed_vault(tmp_path, corpus.build_corpus(8), sidecar=True)
    assert (tmp_path / ".wiki.db").is_file()

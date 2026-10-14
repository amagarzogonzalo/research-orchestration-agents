"""Unit tests: WikiSidecar build/search/rebuild + FileWikiStore integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.infrastructure.wiki import FileWikiStore, WikiSidecar
from knowledge_engine.infrastructure.wiki.sidecar import DB_NAME


def _seed_vault(root: Path) -> None:
    """Create a two-page wiki fixture.

    Args:
        root: Wiki root.
    """
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "rag.md").write_text(
        "---\ntype: source\ntitle: RAG\ndate: 2026-10-09\ntags: retrieval\n---\n\n"
        "Retrieval augmented generation grounds answers in documents " * 6,
        encoding="utf-8",
    )
    (root / "sources" / "aqueducts.md").write_text(
        "---\ntype: source\ntitle: Aqueducts\ndate: 2026-10-09\n---\n\n"
        "Roman aqueduct hydraulics channel water through conduits " * 6 + "links to [[rag]]\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(not WikiSidecar("research_store/wiki").fts_supported(), reason="sqlite build lacks FTS5")
class TestSidecar:
    """FTS5 sidecar behaviors (skipped when sqlite lacks FTS5)."""

    def test_build_and_counts(self, tmp_path: Path) -> None:
        """Building indexes all pages, links and tags."""
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        counts = sidecar.counts()
        assert counts["notes"] == 2
        assert counts["links"] == 1
        assert counts["tags"] == 1

    def test_search_ranks_bm25(self, tmp_path: Path) -> None:
        """A query for retrieval finds the RAG page, best-first."""
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        rows = sidecar.search("retrieval generation grounds", limit=2)
        assert rows
        assert rows[0][0].endswith("rag")

    def test_rebuild_is_idempotent(self, tmp_path: Path) -> None:
        """Two builds produce identical content (derivable, deletable)."""
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        first = sidecar.counts()
        sidecar.build()
        assert sidecar.counts() == first
        # DELETE then rebuild -> same content (the sidecar is a cache).
        (root / DB_NAME).unlink()
        sidecar.build()
        assert sidecar.counts() == first

    def test_hybrid_fuses_fts_and_scan(self, tmp_path: Path) -> None:
        """RRF fusion ranks union of FTS + scan signatures deterministically."""
        from knowledge_engine.infrastructure.wiki.sidecar import _scan_scores

        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        hybrid = sidecar.hybrid_search("retrieval generation", limit=3)
        assert hybrid, "hybrid search must find the RAG page"
        # FTS and scan both rank `rag` first for this query.
        assert hybrid[0][0].endswith("rag")

        # The scan signature alone would also surface it (term overlap).
        scan = _scan_scores(root, "retrieval generation")
        assert scan and scan[0][0] == "rag"

    def test_hybrid_is_deterministic(self, tmp_path: Path) -> None:
        """Two builds produce identical hybrid ranks."""
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        first = sidecar.hybrid_search("aqueduct hydraulics", limit=2)
        sidecar.build()
        second = sidecar.hybrid_search("aqueduct hydraulics", limit=2)
        assert [s for s, _p, _sc in first] == [s for s, _p, _sc in second]
        assert first, "aqueduct page must be findable via fusion"

    def test_hybrid_never_returns_duplicate_pages(self, tmp_path: Path) -> None:
        """FTS (namespaced slugs) + scan (bare stems) fuse per page, not per key.

        Regression: the two rankers key the same page differently
        (``sources/rag`` vs ``rag``); fusion must normalize before merging so
        one page never occupies two result slots.
        """
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        rows = sidecar.hybrid_search("retrieval generation", limit=3)
        assert rows, "hybrid search must find the RAG page"
        stems = [slug.rsplit("/", 1)[-1] for slug, _path, _score in rows]
        assert len(stems) == len(set(stems)), f"duplicate page in hybrid results: {rows}"

    def test_incremental_insert_page(self, tmp_path: Path) -> None:
        """insert_page updates an existing sidecar without full rebuild."""
        root = tmp_path / "wiki"
        _seed_vault(root)
        sidecar = WikiSidecar(root)
        sidecar.build()
        fresh = root / "sources" / "fresh.md"
        fresh.write_text(
            "---\ntype: source\ntitle: Fresh\ndate: 2026-10-09\n---\n\nbrand new wiki content " * 5,
            encoding="utf-8",
        )
        sidecar.insert_page("sources", fresh)
        assert sidecar.counts()["notes"] == 3
        rows = sidecar.search("brand wiki content", limit=1)
        assert rows and rows[0][0].endswith("fresh")


@pytest.mark.skipif(not WikiSidecar("research_store/wiki").fts_supported(), reason="sqlite build lacks FTS5")
async def test_file_store_uses_sidecar_when_present(tmp_path: Path) -> None:
    """search_wiki routes through the sidecar db when one exists on disk."""
    root = tmp_path / "wiki"
    store = FileWikiStore(root=root)
    article = tmp_path / "a.md"
    article.write_text("vector databases store embeddings for retrieval " * 10, encoding="utf-8")
    await store.ingest("t1", "vector databases", str(article))
    WikiSidecar(root).build()
    assert (root / DB_NAME).exists()
    hits = await store.search_wiki("vector embeddings")
    assert hits, "sidecar-backed search must find the filed page"
    assert hits[0].slug

    # Ingest keeps the sidecar in sync (insert_page on next write).
    article2 = tmp_path / "b.md"
    article2.write_text("roman aqueduct hydraulics flowing channels " * 10, encoding="utf-8")
    await store.ingest("t2", "roman aqueducts", str(article2))
    hits2 = await store.search_wiki("aqueduct hydraulics")
    assert hits2 and hits2[0].slug == "roman-aqueducts"


async def test_file_store_falls_back_without_sidecar(tmp_path: Path) -> None:
    """Without a db file the scan-scoring path still answers (degrade-safe)."""
    store = FileWikiStore(root=tmp_path / "wiki")
    article = tmp_path / "a.md"
    article.write_text("quantum error correction codes " * 10, encoding="utf-8")
    await store.ingest("t1", "quantum error correction", str(article))
    hits = await store.search_wiki("quantum error correction")
    assert hits and hits[0].slug

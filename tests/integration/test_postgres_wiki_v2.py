"""Real-Postgres tests for the wiki v2 relational model (TDD, no SQL fakes).

Each test runs against a real Postgres via the ``pg_store``/``pg_conn`` fixtures
(compose/testcontainers/CI service container; skipped when no DB is reachable).
They pin the relational invariants the migration enforces.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from knowledge_engine.infrastructure.wiki import PostgresWikiStore
from knowledge_engine.infrastructure.wiki.postgres.connection import AsyncPgConnectionFactory
from knowledge_engine.infrastructure.wiki.postgres.files import LocalFileStore
from knowledge_engine.schemas.wiki import WikiExtraction, WikiVerdict

psycopg_errors = pytest.importorskip("psycopg.errors")


def _article(tmp_path: Path, text: str) -> str:
    """Write an article file and return its path.

    Args:
        tmp_path: Pytest temp dir.
        text: Article markdown.

    Returns:
        str: Article path.
    """
    path = tmp_path / f"{abs(hash(text))}.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _one(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """Execute SQL and return the first cell of the first row.

    Args:
        cur: A psycopg cursor.
        sql: SQL text.
        params: Parameters.

    Returns:
        Any: First cell, or None.
    """
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


async def test_slug_collision_source_and_query_survive(pg_store, pg_conn, tmp_path) -> None:
    """A source and a query page with the same slug both survive (UNIQUE kind, slug)."""
    from knowledge_engine.infrastructure.wiki import common

    text = "Retrieval augmented generation reduces hallucinations. " * 10
    art = _article(tmp_path, text)
    query = "alpha concept"
    await pg_store.ingest("t-src", query, art)
    await pg_store.file_answer("t-q", query, art)
    slug = common.slugify(query)
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.pages WHERE slug = %s", (slug,)) == 2


async def test_draft_does_not_clobber_approved(pg_store, pg_conn, tmp_path) -> None:
    """compile_extraction drafts must not move current_revision or page status."""
    await pg_store.ingest("t1", "rag concepts", _article(tmp_path, "rag concepts body " * 20))
    await pg_store.compile_extraction(
        "t2", "rag concepts", WikiExtraction(concepts=["rag indexing"], entities=[]), feedback=[]
    )
    with pg_conn.cursor() as cur:
        assert (
            _one(
                cur,
                "SELECT count(*) FROM wiki.revisions r JOIN wiki.pages p ON p.id=r.page_id "
                "WHERE p.slug='rag-indexing' AND r.state='approved'",
            )
            == 0
        )
        assert _one(cur, "SELECT count(*) FROM wiki.revisions WHERE state='approved'") == 1


async def test_approve_supersedes_and_moves_current(pg_store, pg_conn, tmp_path) -> None:
    """A second ingest supersedes the prior approved revision and advances current."""
    await pg_store.ingest("t1", "topic x", _article(tmp_path, "first version content " * 10))
    await pg_store.ingest("t2", "topic x", _article(tmp_path, "second version content superseding " * 10))
    with pg_conn.cursor() as cur:
        state = _one(
            cur,
            "SELECT r.state FROM wiki.revisions r JOIN wiki.pages p ON p.id=r.page_id "
            "WHERE p.slug='topic-x' AND r.id=p.current_revision_id",
        )
        assert state == "approved"
        assert _one(cur, "SELECT count(*) FROM wiki.revisions WHERE state='approved'") == 1


async def test_current_revision_must_belong_to_page(pg_store, pg_conn, tmp_path) -> None:
    """The composite FK rejects a current_revision_id from another page."""
    text = "buffer " * 40
    await pg_store.ingest("t1", "page aa", _article(tmp_path, text))
    await pg_store.file_answer("t2", "page bb answer", _article(tmp_path, text))
    with pg_conn.cursor() as cur:
        ra = _one(
            cur,
            "SELECT r.id FROM wiki.revisions r JOIN wiki.pages p ON p.id=r.page_id WHERE p.kind='source' LIMIT 1",
        )
        pb = _one(cur, "SELECT id FROM wiki.pages WHERE kind='query' LIMIT 1")
        with pytest.raises(psycopg_errors.ForeignKeyViolation):
            cur.execute("UPDATE wiki.pages SET current_revision_id = %s WHERE id = %s", (ra, pb))


async def test_only_one_approved_revision_per_page(pg_store, pg_conn, tmp_path) -> None:
    """The partial unique index keeps at most one approved revision per page."""
    await pg_store.ingest("t1", "unique topic", _article(tmp_path, "content one " * 20))
    await pg_store.ingest("t2", "unique topic", _article(tmp_path, "content two updated " * 20))
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT p.slug, count(*) FROM wiki.revisions r JOIN wiki.pages p ON p.id=r.page_id "
            "WHERE r.state='approved' GROUP BY p.slug"
        )
        rows = cur.fetchall()
        assert rows and all(count == 1 for _, count in rows)


async def test_feedback_roundtrip(pg_store) -> None:
    """record_feedback then feedback_for round-trips rejection lines."""
    await pg_store.record_feedback("rag", "too shallow")
    await pg_store.record_feedback("rag", "cite a paper")
    assert await pg_store.feedback_for("rag") == ["too shallow", "cite a paper"]


async def test_concurrent_duplicate_ingest(pg_store, tmp_path) -> None:
    """Two concurrent identical ingests yield exactly one NEW, one NO_MATERIAL."""
    art = _article(tmp_path, "Deterministic concurrent bytes " * 20)

    async def ingest_once(tag: str):
        return await pg_store.ingest(tag, "concurrent topic", art)

    r1, r2 = await asyncio.gather(ingest_once("tA"), ingest_once("tB"))
    assert sorted([r1.verdict, r2.verdict]) == [WikiVerdict.NEW, WikiVerdict.NO_MATERIAL]


async def test_link_rebuild_and_broken_link_backfill(pg_store, pg_conn, tmp_path) -> None:
    """A link to a not-yet-existing slug is broken, then backfilled on approve."""
    await pg_store.ingest("t1", "alpha page", _article(tmp_path, "alpha body [[beta-topic]] knowledge " * 10))
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.links WHERE dst_page_id IS NULL") == 1
        assert _one(cur, "SELECT count(*) FROM wiki.v_broken_links") == 1
    await pg_store.ingest("t2", "beta topic", _article(tmp_path, "beta now exists " * 20))
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.links WHERE dst_page_id IS NULL") == 0
        assert _one(cur, "SELECT count(*) FROM wiki.v_broken_links") == 0


async def test_strict_then_broad_search_fallback(pg_store, tmp_path) -> None:
    """A query with an absent term gets a broad (OR) match after strict returns none."""
    await pg_store.ingest("t1", "koala habitat", _article(tmp_path, "koalas live in eucalyptus forests " * 10))
    await pg_store.ingest("t2", "other topic", _article(tmp_path, "quantum computing entanglement " * 10))
    hits = await pg_store.search_wiki("koala quantum", limit=3)
    assert hits, "broad fallback must match the koala page"
    assert any(h.slug == "koala-habitat" for h in hits)


async def test_review_after_derived_from_volatility(pg_store, pg_conn, tmp_path) -> None:
    """approve_revision sets review_after from volatility (fast -> 30d TTL)."""
    await pg_store.ingest("t1", "volatile topic", _article(tmp_path, "volatile content " * 20))
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT review_after IS NOT NULL FROM wiki.pages WHERE slug='volatile-topic'") is True


async def test_exact_hit_query_normalized(pg_store, pg_conn, tmp_path) -> None:
    """ingest records a normalized exact-hit query; a recalled search hits directly."""
    await pg_store.ingest("t1", "Exact-Hit! Query?", _article(tmp_path, "exact hit content " * 20))
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT normalized_text FROM wiki.queries") == "exact hit query"
    assert await pg_store.search_wiki("exact hit query")


async def test_files_write_order_idempotent(pg_conn, pg_dsn_str, tmp_path) -> None:
    """Two distinct articles register two files rows + two run_files (article role)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE wiki.events, wiki.revisions, wiki.queries, wiki.links, "
            "wiki.page_tags, wiki.revision_sources, wiki.claims, wiki.source_bodies, "
            "wiki.run_files, wiki.pages, wiki.runs, wiki.tags, wiki.sources CASCADE"
        )
    fs = LocalFileStore(tmp_path / "bodies")
    store = PostgresWikiStore(AsyncPgConnectionFactory.from_dsn(pg_dsn_str), file_store=fs)
    try:
        await store.ingest("t1", "file topic", _article(tmp_path, "file store bytes " * 20))
        await store.ingest("t2", "file topic", _article(tmp_path, "different bytes " * 20))
    finally:
        await store.close()
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.files WHERE bucket='bodies'") == 2
        assert _one(cur, "SELECT count(*) FROM wiki.run_files WHERE role='article'") == 2


async def test_source_bodies_one_current(pg_conn) -> None:
    """The partial unique index allows only one current body per source."""
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO wiki.sources (kind, external_id, title) VALUES ('url','sc1','src') RETURNING id")
        sid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO wiki.files (sha256, path, size_bytes) VALUES (%s, %s, 5)",
            ("1" * 64, "11/" + "1" * 64 + ".md"),
        )
        cur.execute(
            "INSERT INTO wiki.files (sha256, path, size_bytes) VALUES (%s, %s, 6)",
            ("2" * 64, "22/" + "2" * 64 + ".md"),
        )
        cur.execute(
            "INSERT INTO wiki.source_bodies (source_id, file_sha256, tier, is_current) "
            "VALUES (%s, %s, 'manual', true)",
            (sid, "1" * 64),
        )
        with pytest.raises(psycopg_errors.UniqueViolation):
            cur.execute(
                "INSERT INTO wiki.source_bodies (source_id, file_sha256, tier, is_current) "
                "VALUES (%s, %s, 'manual', true)",
                (sid, "2" * 64),
            )


async def test_perf_search_uses_tsv_index(pg_conn) -> None:
    """EXPLAIN confirms the search hits the GIN revisions_tsv_idx at ~1k pages."""
    with pg_conn.cursor() as cur:
        for i in range(1000):
            cur.execute("INSERT INTO wiki.pages (kind, slug) VALUES ('source', %s) RETURNING id", (f"page-{i}",))
            pid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO wiki.revisions (page_id, rev_no, state, title, body_md, content_sha256) "
                "VALUES (%s, 1, 'approved', %s, %s, %s)",
                (
                    pid,
                    f"perfprobe filler text number {i}",
                    f"perfprobe filler text number {i} " * 5,
                    format(i, "064x"),
                ),
            )
        cur.execute(
            "UPDATE wiki.pages p SET current_revision_id = r.id "
            "FROM wiki.revisions r WHERE r.page_id = p.id AND r.rev_no = 1"
        )
        cur.execute("ANALYZE wiki.revisions")
        cur.execute("SET enable_seqscan = off")  # force the planner to consider indexes
        cur.execute(
            "EXPLAIN SELECT p.slug FROM wiki.pages p JOIN wiki.revisions r ON r.id=p.current_revision_id "
            "WHERE r.search_tsv @@ websearch_to_tsquery('english','perfprobe')"
        )
        plan = " ".join(row[0] for row in cur.fetchall())
        assert "revisions_tsv_idx" in plan, f"expected a GIN scan on revisions_tsv_idx, got: {plan}"


async def test_record_sources_links_to_revision(pg_store, pg_conn, tmp_path) -> None:
    """record_sources upserts wiki.sources and links them to the producing revision."""
    from knowledge_engine.schemas.source import SearchSource, SourceRecord

    await pg_store.ingest("t-src", "source topic", _article(tmp_path, "source topic body " * 20))
    sources = [
        SourceRecord(
            id="openalex:W1",
            title="RAG paper",
            source=SearchSource.OPENALEX,
            doi="10.1000/rags",
            authors=["A", "B"],
            year=2023,
            venue="ACL",
        ),
        SourceRecord(id="arxiv:2301.00001", title="Hybrid retrieval", source=SearchSource.ARXIV, year=2023),
    ]
    n = await pg_store.record_sources("t-src", sources)
    assert n == 2
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.sources") == 2
        assert _one(cur, "SELECT count(*) FROM wiki.revision_sources") == 2
        assert _one(cur, "SELECT kind FROM wiki.sources WHERE external_id='openalex:W1'") == "doi"


async def test_record_sources_idempotent(pg_store, pg_conn, tmp_path) -> None:
    """Re-recording the same sources upserts in place (unique kind+external_id)."""
    from knowledge_engine.schemas.source import SearchSource, SourceRecord

    await pg_store.ingest("t-id", "idem topic", _article(tmp_path, "idem body " * 20))
    src = SourceRecord(id="openalex:W9", title="One paper", source=SearchSource.OPENALEX, year=2022)
    await pg_store.record_sources("t-id", [src])
    await pg_store.record_sources("t-id", [src])
    with pg_conn.cursor() as cur:
        assert _one(cur, "SELECT count(*) FROM wiki.sources") == 1
        assert _one(cur, "SELECT count(*) FROM wiki.revision_sources") == 1

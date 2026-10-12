"""Loop 2 proof: PostgresWikiStore search is server-side — 1 fetch, not N.

The Supabase adapter lists the bucket and downloads every page to score it
(O(N) reads per query, supabase_store.py:123,131). The Postgres adapter runs a
single ranked FTS ``SELECT``; this test counts how many result-set fetches a
search performs and asserts it stays O(1) regardless of page count.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakes import fake_pg_connection  # noqa: E402

from knowledge_engine.infrastructure.wiki.postgres_store import PostgresWikiStore  # noqa: E402
from knowledge_engine.schemas.wiki import WikiVerdict  # noqa: E402


def _article(tmp_path: Path, text: str) -> str:
    """Write a fixture article and return its path.

    Args:
        tmp_path: Per-test temp dir.
        text: Article markdown.

    Returns:
        str: Article path.
    """
    path = tmp_path / "article.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


async def test_search_fetches_one_result_set_regardless_of_page_count(tmp_path: Path) -> None:
    """A search costs exactly one result-set fetch even at N pages (O(1))."""
    conn = fake_pg_connection()
    store = PostgresWikiStore(lambda: conn)

    for i in range(25):
        result = await store.ingest(f"t{i}", f"irrelevant topic {i}", _article(tmp_path, f"filler bytes {i}"))
        assert result.verdict == WikiVerdict.NEW

    baseline = conn.reads
    hits = await store.search_wiki("irrelevant")
    assert hits, "match must be found"

    # The bounded Supabase path would download all ~25 pages (>=25 reads);
    # server-side FTS reads exactly one ranked result set.
    assert conn.reads - baseline == 1
    assert len(hits) <= 3


async def test_search_ranks_and_limits_server_side(tmp_path: Path) -> None:
    """Only matching active pages return, ranked, bounded by the limit."""
    conn = fake_pg_connection()
    store = PostgresWikiStore(lambda: conn)
    await store.ingest("t1", "vector databases", _article(tmp_path, "vector databases for retrieval"))
    await store.ingest("t2", "unrelated cuisine", _article(tmp_path, "pasta recipes here"))
    await store.ingest("t3", "rag hallucinations", _article(tmp_path, "rag reduces hallucinations"))

    hits = await store.search_wiki("vector databases", limit=2)
    slugs = [h.slug for h in hits]
    assert slugs == ["vector-databases"], "best match is ranked first and non-matches excluded"
    assert len(hits) == 1

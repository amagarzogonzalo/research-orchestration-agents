"""Live integration: PostgresWikiStore on real Supabase Postgres (issue #31).

Requires ``DATABASE_URL`` (or ``SUPABASE_DB_URL``) pointing at the Supabase
database; skipped otherwise and excluded from the default `make test` via the
``live`` marker — see pyproject markers.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")),
        reason="DATABASE_URL/SUPABASE_DB_URL not configured",
    ),
]


async def test_postgres_wiki_roundtrip_real_postgres(tmp_path) -> None:
    """Ingest -> search round trip on real Postgres (one query, not N)."""
    import uuid

    from knowledge_engine.infrastructure.wiki.postgres_store import PostgresWikiStore
    from knowledge_engine.schemas.wiki import WikiVerdict

    store = PostgresWikiStore.from_env()
    tag = uuid.uuid4().hex[:8]
    thread_id = f"live-pg-wiki-{tag}"
    query = f"live postgres wiki probe {tag}"
    article = tmp_path / "article.md"
    article.write_text(f"relational wiki roundtrip content {tag} " * 20, encoding="utf-8")
    result = await store.ingest(thread_id, query, str(article))
    assert result.verdict == WikiVerdict.NEW

    hits = await store.search_wiki(query)
    assert hits, "uploaded page must be searchable via FTS"

    again = await store.ingest(thread_id, query, str(article))
    assert again.verdict == WikiVerdict.NO_MATERIAL
    assert again.pages_touched == 0
    assert again.content_sha256 == result.content_sha256

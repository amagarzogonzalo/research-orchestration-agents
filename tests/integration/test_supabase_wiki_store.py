"""Live integration: Supabase wiki store + linter round trip on real storage.

Requires ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY`` (skipped otherwise;
excluded from the default `make test` via the ``live`` marker semantics —
see pyproject markers).
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")),
        reason="SUPABASE_URL/SUPABASE_SECRET_KEY not configured",
    ),
]


async def test_supabase_wiki_roundtrip_real_storage(tmp_path) -> None:
    """Ingest, search, and lint a real bucket object end to end."""
    import uuid

    from knowledge_engine.infrastructure.wiki import SupabaseVaultLinter, SupabaseWikiStore

    store = SupabaseWikiStore.from_env()
    # Unique thread + query per run: real storage persists across runs, so a
    # fixed probe would collide (cross-thread content dedup -> no_material,
    # same slug -> update). A fresh pair guarantees NEW deterministically.
    tag = uuid.uuid4().hex[:8]
    thread_id = f"live-wiki-probe-{tag}"
    query = f"live wiki probe {tag}"
    article = tmp_path / "article.md"
    article.write_text(f"live integration roundtrip content {tag} " * 20, encoding="utf-8")
    result = await store.ingest(thread_id, query, str(article))
    assert result.verdict.value == "new"

    hits = await store.search_wiki(query)
    assert hits, "uploaded page must be searchable"

    report = await SupabaseVaultLinter(store).lint()
    assert report.pages >= 1

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
    from knowledge_engine.infrastructure.wiki import SupabaseVaultLinter, SupabaseWikiStore

    store = SupabaseWikiStore.from_env()
    article = tmp_path / "article.md"
    article.write_text("live integration roundtrip content " * 20, encoding="utf-8")
    result = await store.ingest("live-wiki-probe", "live wiki probe", str(article))
    assert result.verdict.value == "new"

    hits = await store.search_wiki("live wiki probe")
    assert hits, "uploaded page must be searchable"

    report = await SupabaseVaultLinter(store).lint()
    assert report.pages >= 1

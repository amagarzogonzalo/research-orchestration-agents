"""Unit tests: SupabaseWikiStore against a fake client (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fakes import fake_supabase_client  # noqa: E402

from knowledge_engine.core.exceptions import ConfigurationError  # noqa: E402
from knowledge_engine.infrastructure.wiki.supabase_store import SupabaseWikiStore  # noqa: E402
from knowledge_engine.schemas.wiki import WikiVerdict  # noqa: E402


async def test_ingest_uploads_and_search_finds(tmp_path: Path) -> None:
    """A filed page lands in the bucket and is searchable."""
    store = SupabaseWikiStore(fake_supabase_client({}), bucket="wiki")
    article = tmp_path / "article.md"
    article.write_text("vector databases store embeddings for retrieval", encoding="utf-8")
    result = await store.ingest("t1", "vector databases", str(article))
    assert result.verdict == WikiVerdict.NEW
    hits = await store.search_wiki("vector databases")
    assert hits and hits[0].slug


async def test_from_env_requires_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing env keys raise ConfigurationError (no-op-until-configured)."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        SupabaseWikiStore.from_env()


async def test_no_material_on_repeat(tmp_path: Path) -> None:
    """Same thread + same bytes -> No-material, zero page writes."""
    store = SupabaseWikiStore(fake_supabase_client({}), bucket="wiki")
    article = tmp_path / "a.md"
    article.write_text("stable content", encoding="utf-8")
    await store.ingest("t1", "stable topic", str(article))
    again = await store.ingest("t1", "stable topic", str(article))
    assert again.verdict == WikiVerdict.NO_MATERIAL

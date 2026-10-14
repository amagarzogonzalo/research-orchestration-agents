"""Unit tests: SupabaseWikiStore against a fake client (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fakes import fake_supabase_client  # noqa: E402

from knowledge_engine.core.exceptions import ConfigurationError  # noqa: E402
from knowledge_engine.infrastructure.wiki.supabase_store import SupabaseWikiStore  # noqa: E402
from knowledge_engine.schemas.wiki import WikiThesis, WikiVerdict  # noqa: E402


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


async def test_file_thesis_updates_index(tmp_path: Path) -> None:
    """A filed thesis page gains an index entry (no index drift).

    Regression: file_thesis uploaded to synthesis/ but never updated
    index.md, leaving the page unindexed and the vault drifting.
    """
    client = fake_supabase_client({})
    store = SupabaseWikiStore(client, bucket="wiki")
    await store.file_thesis(
        "t1",
        WikiThesis(
            claim="RAG reduces hallucinations",
            for_claims=["grounding"],
            against_claims=["noise"],
            verdict="supported",
            sources=[],
        ),
    )
    index = client._store.get("index.md", "")
    assert "synthesis/rag-reduces-hallucinations.md" in index


async def test_file_answer_updates_index(tmp_path: Path) -> None:
    """A filed answer gains an index entry under the Queries section.

    Regression: file_answer uploaded to queries/ but never updated
    index.md, leaving the page unindexed and the vault drifting.
    """
    client = fake_supabase_client({})
    store = SupabaseWikiStore(client, bucket="wiki")
    article = tmp_path / "article.md"
    article.write_text("a rendered answer with enough body text " * 10, encoding="utf-8")
    await store.file_answer("t1", "what is rag", str(article))
    index = client._store.get("index.md", "")
    assert "queries/what-is-rag.md" in index


async def test_search_finds_filed_answer_and_thesis(tmp_path: Path) -> None:
    """search_wiki covers queries/ and synthesis/, like the file store.

    Regression: search listed only the pages/ prefix, so filed answers and
    thesis verdicts were unretrievable from the cloud wiki.
    """
    client = fake_supabase_client({})
    store = SupabaseWikiStore(client, bucket="wiki")
    article = tmp_path / "article.md"
    article.write_text("retrieval augmented generation grounds answers in documents " * 10, encoding="utf-8")
    await store.file_answer("t1", "retrieval augmented generation grounding", str(article))
    await store.file_thesis(
        "t1",
        WikiThesis(
            claim="retrieval grounding reduces hallucinations",
            for_claims=["citations"],
            against_claims=[],
            verdict="supported",
            sources=[],
        ),
    )
    answer_hits = await store.search_wiki("retrieval augmented generation grounding")
    assert [h for h in answer_hits if h.slug == "retrieval-augmented-generation-grounding"]
    thesis_hits = await store.search_wiki("retrieval grounding reduces hallucinations")
    assert [h for h in thesis_hits if h.slug == "retrieval-grounding-reduces-hallucinations"]

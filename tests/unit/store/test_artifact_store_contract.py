"""ArtifactStorePort contract suite (T1.1, issue #17 / ADR-0012).

Every adapter must pass the SAME behaviour suite — this is the LSP proof that
Local and Supabase are drop-in substitutes. Adapters are sync (ADR-0012
trade-off): file writes are already sync, supabase-py is sync.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from tests.conftest import FakeArtifactStore


def _artifact(body: str) -> KnowledgeArtifact:
    """Build a minimal artifact.

    Args:
        body: Body markdown.

    Returns:
        KnowledgeArtifact: Test artifact.
    """
    return KnowledgeArtifact(
        id="t1", title="Test", body_markdown=body, frontmatter_yaml="title: Test\n", generated_at=""
    )


def _sources() -> list[SourceRecord]:
    """Build two source records.

    Returns:
        list[SourceRecord]: Test sources.
    """
    return [
        SourceRecord(id="openalex:W1", title="One", source=SearchSource.OPENALEX),
        SourceRecord(id="openalex:W2", title="Two", source=SearchSource.OPENALEX),
    ]


def test_put_then_get_article_roundtrips(tmp_path) -> None:
    """LocalArtifactStore reproduces today's on-disk bytes (contract A)."""
    from knowledge_engine.store.artifact_store import LocalArtifactStore

    store = LocalArtifactStore(root=tmp_path)
    locator = store.put_article("t1", _artifact("hello"))
    assert "t1" in locator
    stored = store.get_article("t1") or ""
    assert "hello" in stored


def test_local_article_bytes_match_legacy_layout(tmp_path) -> None:
    """The local adapter writes the same article.md layout ResearchStore had."""
    from knowledge_engine.store.artifact_store import LocalArtifactStore

    store = LocalArtifactStore(root=tmp_path)
    store.put_article("t1", _artifact("hello"))
    text = (tmp_path / "t1" / "article.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Test\n")


def test_put_sources_then_get_roundtrips(tmp_path) -> None:
    """sources.json bytes persist and reload (contract B)."""
    from knowledge_engine.store.artifact_store import LocalArtifactStore

    store = LocalArtifactStore(root=tmp_path)
    locator = store.put_sources("t1", _sources())
    assert "t1" in locator
    assert len(store.get_sources("t1")) == 2


def test_get_article_missing_returns_none(tmp_path) -> None:
    """A missing thread reads as None, never raises (contract C)."""
    from knowledge_engine.store.artifact_store import LocalArtifactStore

    store = LocalArtifactStore(root=tmp_path)
    assert store.get_article("missing") is None


def test_fake_store_is_a_spy_like_substitute(tmp_path) -> None:
    """FakeArtifactStore satisfies the same contract (offline tests)."""
    store = FakeArtifactStore()
    store.put_article("t1", _artifact("hello"))
    stored = store.get_article("t1") or ""
    assert "hello" in stored
    assert store.calls


@pytest.mark.parametrize(
    "factory",
    [
        lambda tmp: FakeArtifactStore(),
        lambda tmp: __import__(
            "knowledge_engine.store.artifact_store", fromlist=["LocalArtifactStore"]
        ).LocalArtifactStore(root=tmp),
    ],
    ids=["fake", "local"],
)
def test_contract_uniform_across_adapters(factory: Callable, tmp_path) -> None:
    """The LSP proof: one suite, every adapter (drop-in substitutability)."""
    store = factory(tmp_path)
    locator = store.put_article("t1", _artifact("body"))
    assert "t1" in locator
    stored = store.get_article("t1") or ""
    assert "body" in stored
    store.put_sources("t1", _sources())
    assert len(store.get_sources("t1")) == 2
    assert store.get_article("missing") is None

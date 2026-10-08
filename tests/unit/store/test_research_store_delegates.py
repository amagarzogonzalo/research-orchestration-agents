"""ResearchStore delegation (T2.1, issue #17 / ADR-0012): DIP through the port.

ResearchStore orchestrates run folders; PUBLISHED-artifact persistence is the
port's job. Default (None) preserves today's byte-identical local behavior.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.research_store import ResearchStore


def _artifact() -> KnowledgeArtifact:
    """Build a minimal artifact.

    Returns:
        KnowledgeArtifact: Test artifact.
    """
    return KnowledgeArtifact(
        id="t1", title="Test", body_markdown="body", frontmatter_yaml="title: Test\n", generated_at=""
    )


class SpyArtifactStore:
    """Records calls; satisfies ArtifactStorePort-shaped surface."""

    def __init__(self) -> None:
        self.article_calls: list[tuple[str, str]] = []
        self.sources_calls: list[tuple[str, int]] = []

    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        self.article_calls.append((thread_id, artifact.body_markdown))
        return f"spy://articles/{thread_id}"

    def put_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        self.sources_calls.append((thread_id, len(sources)))
        return f"spy://sources/{thread_id}"

    def get_article(self, thread_id: str) -> str | None:
        return next((a for t, a in self.article_calls if t == thread_id), None)

    def get_sources(self, thread_id: str) -> list[SourceRecord]:
        return []


def test_write_article_delegates_and_keeps_local_copy(tmp_path) -> None:
    """Delegation happens AND the backward-compatible local copy still exists."""
    spy = SpyArtifactStore()
    store = ResearchStore(Path(tmp_path), artifacts=spy)  # type: ignore[arg-type]
    store.write_article(_artifact(), "t1")
    assert spy.article_calls == [("t1", "body")]
    assert (tmp_path / "t1" / "article.md").exists(), "local copy preserved"


def test_write_sources_delegates(tmp_path) -> None:
    """write_sources routes through the port."""
    spy = SpyArtifactStore()
    store = ResearchStore(Path(tmp_path), artifacts=spy)  # type: ignore[arg-type]
    sources = [SourceRecord(id="openalex:W1", title="One", source=SearchSource.OPENALEX)]
    store.write_sources("t1", sources)
    assert spy.sources_calls == [("t1", 1)]
    assert (tmp_path / "t1" / "sources.json").exists(), "local copy preserved"


def test_default_ctor_keeps_local_behavior(tmp_path) -> None:
    """No port passed: byte-identical legacy behavior (evals stay green)."""
    store = ResearchStore(Path(tmp_path))
    store.write_article(_artifact(), "t1")
    text = (tmp_path / "t1" / "article.md").read_text(encoding="utf-8")
    assert "body" in text

"""The render path persists through ArtifactStorePort (ADR-0012 T5, DIP).

RenderNode previously wrote article.md via its own ``_default_save`` bypassing
the port; the composition root now injects a save callable backed by
``ResearchStore.write_article`` so EVERY artifact write flows through
LocalArtifactStore (default) or Supabase (opt-in) transparently.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.engine import ResearchEngine
from tests.conftest import FakeArtifactStore, FakeProvider, FakeScrape, FakeSearch


async def test_research_artifact_flows_through_the_port(tmp_path) -> None:
    """research() with an injected port delegates the published artifact."""
    fake = FakeArtifactStore()
    engine = ResearchEngine(
        store_root=str(tmp_path / "runs"),
        artifacts=fake,
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=FakeProvider(),
    )
    result = await engine.research("multi-agent orchestration", workflow="research", thread_id="port-e2e")
    assert result.state.status.value == "done"
    assert "port-e2e:article" in fake.calls, "artifact persisted via the port"
    assert "port-e2e:sources" in fake.calls, "sources persisted via the port"
    stored = fake.get_article("port-e2e") or ""
    assert stored, "article retrievable from the port"
    assert Path(str(result.state.output_path)).exists(), "local copy preserved (backward-compatible)"


async def test_research_default_local_store_writes_article(tmp_path) -> None:
    """Default local artifact store keeps the render path byte-compatible."""
    engine = ResearchEngine(
        store_root=str(tmp_path / "runs"),
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=FakeProvider(),
    )
    result = await engine.research("multi-agent orchestration", workflow="research", thread_id="local-e2e")
    assert result.state.status.value == "done"
    article = Path(tmp_path / "runs" / "local-e2e" / "article.md")
    assert article.exists(), "legacy article.md path preserved"

"""E2E: research() persists the article through Supabase Storage (ADR-0012 T5.1).

Gated by ``RUN_LIVE_E2E=1``. The WORKFLOW is fully offline (fakes for
LLM/search/scrape — zero quota); only the artifact write/read touches real
Storage, proving the publish path end-to-end through the composition root.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import FakeArtifactStore  # noqa: F401 - proves the port contract


@pytest.mark.live
async def test_engine_research_persists_to_supabase(tmp_path) -> None:
    """research() artifact lands in Storage and re-reads via the port."""
    if os.environ.get("RUN_LIVE_E2E", "") != "1":
        pytest.skip("set RUN_LIVE_E2E=1 to run the Supabase e2e")
    from knowledge_engine.config import load_dotenv

    load_dotenv()
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
        pytest.skip("SUPABASE_URL/SUPABASE_SECRET_KEY not configured")

    from knowledge_engine.engine import ResearchEngine
    from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore
    from tests.conftest import FakeProvider, FakeScrape, FakeSearch

    engine = ResearchEngine(
        store_root=str(tmp_path / "runs"),
        artifact_store_kind="supabase",
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=FakeProvider(),
    )
    result = await engine.research("multi-agent orchestration", workflow="research", thread_id="supabase-e2e")
    assert result.state.status.value == "done"

    store = SupabaseArtifactStore.from_env()
    stored = store.get_article("supabase-e2e") or ""
    assert stored, "article must persist to Supabase Storage"
    assert stored.startswith("---"), "frontmatter present"
    assert "type: KnowledgeBaseArticle" in stored, "schema frontmatter present"

    sources = store.get_sources("supabase-e2e")
    assert sources, "sources.json persisted to Storage"

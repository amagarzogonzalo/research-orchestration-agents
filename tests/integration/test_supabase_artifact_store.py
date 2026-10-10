"""SupabaseArtifactStore round-trip against REAL Storage (opt-in live test).

Runs in the migration loops via ``RUN_LIVE_E2E=1`` (repo convention from
``tests/e2e/test_live.py``); skips everywhere else so CI stays keyless-green.
"""

from __future__ import annotations

import os

import pytest

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _gated() -> None:
    if os.environ.get("RUN_LIVE_E2E", "") != "1":
        pytest.skip("set RUN_LIVE_E2E=1 to run live Storage round-trips")
    from knowledge_engine.config import load_dotenv

    load_dotenv()
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
        pytest.skip("SUPABASE_URL/SUPABASE_SECRET_KEY not configured")


def _artifact(body: str) -> KnowledgeArtifact:
    return KnowledgeArtifact(
        id="ci-probe", title="CI", body_markdown=body, frontmatter_yaml="title: CI\n", generated_at=""
    )


def test_supabase_artifact_roundtrip_real_storage() -> None:
    _gated()
    import httpx

    from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore

    store = SupabaseArtifactStore.from_env()
    locator = store.put_article("migration-probe", _artifact(f"probe {os.urandom(4).hex()}"))
    assert locator.startswith("http"), "locator is a Storage URL"
    stored = store.get_article("migration-probe") or ""
    assert "probe" in stored, "bytes survive the round-trip"
    public = httpx.get(locator)
    assert public.status_code == 200, f"published artifact must be viewable without auth ({locator})"
    assert "probe" in public.text, "public bytes match the artifact"
    # Supabase serves public objects with XSS protection (html -> text/plain),
    # so the .md plain-text IS the cloud read surface; the rendered HTML twin
    # is published locally by LocalArtifactStore.


def test_supabase_sources_roundtrip_real_storage() -> None:
    _gated()
    from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore

    store = SupabaseArtifactStore.from_env()
    sources = [SourceRecord(id="openalex:W1", title="One", source=SearchSource.OPENALEX)]
    store.put_sources("migration-probe", sources)
    loaded = store.get_sources("migration-probe")
    assert len(loaded) == 1 and loaded[0].id == "openalex:W1"

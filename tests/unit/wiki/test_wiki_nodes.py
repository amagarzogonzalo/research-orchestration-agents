"""Unit tests: WikiQueryNode + WikiIngestNode purity and routing writes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeWikiStore  # noqa: E402

from knowledge_engine.orchestration.nodes.wiki_ingest import WikiIngestNode  # noqa: E402
from knowledge_engine.orchestration.nodes.wiki_query import WikiQueryNode  # noqa: E402


async def test_query_hit_writes_notes_and_flag() -> None:
    """An exact-query hit records hits, sets wiki_hit, and loads the page."""
    store = FakeWikiStore(pages={"rag-hallucinations": "wiki page about rag hallucinations"})
    await store.ingest("t1", "rag hallucinations", "article-bytes")
    node = WikiQueryNode(store)
    out = await node.run({"query": "rag hallucinations", "thread_id": "t1"})
    assert out["wiki_hit"] is True
    assert out["wiki_hits"]
    assert out["notes"] == ["---\ntype: source\n---\n\nrag-hallucinations"]


async def test_scored_only_hit_is_observability_only() -> None:
    """A term-overlap match without the exact query sha does NOT route."""
    store = FakeWikiStore(pages={"rag-page": "wiki page about rag hallucinations"})
    node = WikiQueryNode(store)
    out = await node.run({"query": "rag hallucinations", "thread_id": "t1"})
    assert "wiki_hit" not in out
    assert out["wiki_hits"]


async def test_query_miss_writes_nothing() -> None:
    """A miss is silent: empty dict, no state changes, replay-safe."""
    store = FakeWikiStore()
    node = WikiQueryNode(store)
    out = await node.run({"query": "quantum tunneling", "thread_id": "t1"})
    assert out == {}
    assert store.calls_search == 1


async def test_query_hit_does_not_call_live_search() -> None:
    """The wiki hit path never touches the live search port (quota saved)."""

    class SpySearch:
        calls = 0

        async def search(self, query: str, *, limit: int = 10) -> list:
            """Record a live-search attempt.

            Args:
                query: Query string.
                limit: Max records.

            Returns:
                list: Empty (should never be called).
            """
            SpySearch.calls += 1
            return []

    store = FakeWikiStore(pages={"rag": "rag page"})
    await store.ingest("t1", "rag", "article-bytes")
    node = WikiQueryNode(store)
    await node.run({"query": "rag", "thread_id": "t1"})
    assert SpySearch.calls == 0


async def test_ingest_node_delegates_when_output_exists() -> None:
    """WikiIngestNode calls the port when the run rendered an artifact."""
    store = FakeWikiStore()
    node = WikiIngestNode(store)
    out = await node.run({"thread_id": "t1", "query": "rag", "output_path": "research_store/t1/article.md"})
    assert store.calls_ingest == 1
    assert out["wiki_ingest"]["verdict"] == "new"


async def test_ingest_node_skips_failed_runs() -> None:
    """No rendered artifact -> no wiki writes (failed runs never compile)."""
    store = FakeWikiStore()
    node = WikiIngestNode(store)
    out = await node.run({"thread_id": "t1", "query": "rag"})
    assert out == {}
    assert store.calls_ingest == 0

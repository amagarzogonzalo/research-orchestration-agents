"""Integration tests: deep workflow compiles + runs offline (TDD red, fakes only)."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.orchestration.graphs import NodeSet, build_workflow
from knowledge_engine.orchestration.nodes import (
    BodiesNode,
    FetchBodyNode,
    IngestNode,
    JudgeNode,
    RelevanceNode,
    RenderNode,
    ReviewNode,
    SynthesizeNode,
)
from knowledge_engine.orchestration.nodes.aggregate import AggregateNode
from knowledge_engine.orchestration.nodes.branch import BranchNode
from knowledge_engine.orchestration.nodes.critic import CriticNode
from knowledge_engine.orchestration.nodes.decompose import DecomposeNode
from knowledge_engine.orchestration.nodes.intent import IntentNode
from knowledge_engine.orchestration.runtime import GraphRuntime
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _deep_nodes(tmp_path, *, search=None) -> NodeSet:
    search = search or FakeSearch()
    scrape, provider = FakeScrape(), FakeProvider()
    storage = LocalBodyStorage(root=str(Path(tmp_path) / "bodies"))
    ingest, relevance = IngestNode(search), RelevanceNode(provider)
    fetch_body, synthesize = FetchBodyNode(scrape, storage=storage), SynthesizeNode(provider, storage=storage)
    return NodeSet(
        ingest=ingest,
        relevance=relevance,
        bodies=BodiesNode(),
        fetch_body=fetch_body,
        synthesize=synthesize,
        judge=JudgeNode(provider),
        review=ReviewNode(),
        render=RenderNode(root=str(tmp_path)),
        intent=IntentNode(provider=provider),
        decompose=DecomposeNode(provider=provider),
        branch=BranchNode(ingest=ingest, relevance=relevance, fetch_body=fetch_body, synthesize=synthesize),
        aggregate=AggregateNode(provider=provider),
        critic=CriticNode(provider=provider),
    )


def test_deep_workflow_builds(tmp_path) -> None:
    graph = build_workflow("deep", _deep_nodes(tmp_path), checkpointer=InMemorySaver())
    assert set(graph.get_graph().nodes) >= {"intent", "decompose", "branch", "aggregate"}


async def test_deep_run_fans_out_and_terminates(tmp_path) -> None:
    runtime = GraphRuntime(nodes=_deep_nodes(tmp_path), checkpointer=InMemorySaver())
    result = await runtime.run("deep", "t-deep", "compare LLM memory architectures, evaluation, and limitations")
    assert result.state.synthetic_notes
    assert not result.interrupted
    assert result.state.iterations >= 0


async def test_deep_run_short_circuits_non_deep_intent(tmp_path) -> None:
    search = FakeSearch()
    runtime = GraphRuntime(nodes=_deep_nodes(tmp_path, search=search), checkpointer=InMemorySaver())
    result = await runtime.run("deep", "t-chat", "hi")
    assert result.state.synthetic_notes
    assert search.calls == 0


async def test_deep_explicit_mode_beats_quick_heuristic(tmp_path) -> None:
    """An explicitly requested deep run must research, not fast-path to answer."""
    search = FakeSearch()
    runtime = GraphRuntime(nodes=_deep_nodes(tmp_path, search=search), checkpointer=InMemorySaver())
    result = await runtime.run("deep", "t-forced", "how to scale a research agent to the cloud free?", mode="deep")
    assert search.calls > 0
    assert result.state.synthetic_notes


async def test_branch_fetches_web_tier_alongside_scholarly(tmp_path) -> None:
    """Branch pool fetches the web URL live (offline proof with fakes)."""
    from knowledge_engine.orchestration.nodes.branch import BranchNode
    from knowledge_engine.schemas.source import SearchSource, SourceRecord

    search = FakeSearch(
        sources=[
            SourceRecord(
                id="openalex:W0",
                title="Paper 0",
                source=SearchSource.OPENALEX,
                pdf_url="https://example.org/W0.pdf",
            ),
            SourceRecord(
                id="openalex:W1",
                title="Paper 1",
                source=SearchSource.OPENALEX,
                pdf_url="https://example.org/W1.pdf",
            ),
            SourceRecord(
                id="openalex:W2",
                title="Paper 2",
                source=SearchSource.OPENALEX,
                pdf_url="https://example.org/W2.pdf",
            ),
            SourceRecord(
                id="duckduckgo:https://ex.io/guide", title="Practitioner guide", source=SearchSource.DUCKDUCKGO
            ),
        ]
    )
    scrape, provider = FakeScrape(), FakeProvider()
    storage = LocalBodyStorage(root=str(Path(tmp_path) / "bodies"))
    ingest, relevance = IngestNode(search), RelevanceNode(provider)
    fetch_body, synthesize = FetchBodyNode(scrape, storage=storage), SynthesizeNode(provider, storage=storage)
    branch = BranchNode(ingest=ingest, relevance=relevance, fetch_body=fetch_body, synthesize=synthesize)
    out = await branch.run({"query": "how to scale free?", "sub_query": {"query": "how to scale free?"}})
    assert "https://ex.io/guide" in scrape.calls
    assert len(out["bodies"]) >= 4


class ConcurrentSearch(FakeSearch):
    """Measures peak in-flight search concurrency (parallelism proof)."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0

    async def search(self, query: str, *, limit: int = 10):  # type: ignore[override]
        import asyncio

        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return await super().search(query, limit=limit)
        finally:
            self.in_flight -= 1


async def test_deep_branches_run_concurrently(tmp_path) -> None:
    search = ConcurrentSearch()
    runtime = GraphRuntime(nodes=_deep_nodes(tmp_path, search=search), checkpointer=InMemorySaver())
    result = await runtime.run("deep", "t-conc", "compare LLM memory architectures, evaluation, and limitations")
    assert result.state.synthetic_notes
    assert search.peak >= 2

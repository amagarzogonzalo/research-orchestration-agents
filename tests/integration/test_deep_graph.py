"""Integration tests: deep workflow compiles + runs offline (TDD red, fakes only)."""

from __future__ import annotations

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
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _deep_nodes(tmp_path, *, search=None) -> NodeSet:
    search = search or FakeSearch()
    scrape, provider = FakeScrape(), FakeProvider()
    ingest, relevance = IngestNode(search), RelevanceNode(provider)
    fetch_body, synthesize = FetchBodyNode(scrape), SynthesizeNode(provider)
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

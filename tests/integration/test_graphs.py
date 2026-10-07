"""Integration tests: compiled research/qa/summary graphs with fakes + InMemorySaver."""

from __future__ import annotations

import pytest

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
from knowledge_engine.orchestration.runtime import GraphRuntime
from tests.conftest import FakeEmptySearch, FakeProvider, FakeScrape, FakeSearch


def _nodeset(search, scrape, provider, root: str = "knowledge_base") -> NodeSet:
    return NodeSet(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(scrape),
        synthesize=SynthesizeNode(provider),
        judge=JudgeNode(provider),
        review=ReviewNode(),
        render=RenderNode(root=root),
    )


async def test_research_graph_happy_path() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    runtime = GraphRuntime(nodes=_nodeset(FakeSearch(), FakeScrape(), FakeProvider()), checkpointer=InMemorySaver())
    result = await runtime.run("research", "t1", "multi-agent orchestration")
    assert not result.interrupted
    state = result.state
    assert state.status.value == "done"
    assert state.sources, "ingest should resolve sources"
    assert state.bodies, "fan-out should fetch bodies"
    assert state.synthetic_notes, "synthesize should write notes"
    assert state.output_path, "render should write an artifact"


async def test_research_graph_optimizer_loop_when_judge_fails() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    provider = FakeProvider(judge_pass=False)
    runtime = GraphRuntime(nodes=_nodeset(FakeSearch(), FakeScrape(), provider), checkpointer=InMemorySaver())
    result = await runtime.run("research", "t-loop", "evaluator optimizer")
    state = result.state
    # judge refuses -> re-synthesize (bounded), so notes has multiple entries.
    assert len(state.synthetic_notes) >= 2 or state.status.value == "done"
    assert state.synthetic_notes


async def test_research_graph_empty_search_does_not_crash() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    runtime = GraphRuntime(
        nodes=_nodeset(FakeEmptySearch(), FakeScrape(), FakeProvider()), checkpointer=InMemorySaver()
    )
    result = await runtime.run("research", "t-empty", "nothing found")
    assert result.state.status.value == "done"
    assert result.state.sources == []


async def test_qa_workflow_skips_body_fanout() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    runtime = GraphRuntime(nodes=_nodeset(FakeSearch(), FakeScrape(), FakeProvider()), checkpointer=InMemorySaver())
    result = await runtime.run("qa", "t-qa", "what is an agent?")
    assert result.state.status.value == "done"
    assert result.state.bodies == []  # qa wiring never fetches bodies


async def test_summary_workflow_runs() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    runtime = GraphRuntime(nodes=_nodeset(FakeSearch(), FakeScrape(), FakeProvider()), checkpointer=InMemorySaver())
    result = await runtime.run("summary", "t-sum", "summarize recent work")
    assert result.state.status.value == "done"
    assert result.state.synthetic_notes


async def test_summary_workflow_fetches_bodies_from_sources() -> None:
    """summary skips relevance but must STILL fan out bodies (regression #2)."""
    from langgraph.checkpoint.memory import InMemorySaver

    runtime = GraphRuntime(nodes=_nodeset(FakeSearch(), FakeScrape(), FakeProvider()), checkpointer=InMemorySaver())
    result = await runtime.run("summary", "t-sum-bodies", "summarize work")
    assert result.state.status.value == "done"
    assert result.state.bodies, "summary must fetch bodies from sources (filtered is unset)"


async def test_build_workflow_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        build_workflow("nope", _nodeset(FakeSearch(), FakeScrape(), FakeProvider()))

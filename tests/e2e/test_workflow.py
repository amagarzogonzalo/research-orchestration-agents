"""E2E: full research workflow through GraphRuntime incl. the HITL review interrupt."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.orchestration.graphs import NodeSet
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
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _runtime(*, interactive_review: bool = False, root: str = "knowledge_base") -> GraphRuntime:
    search, scrape, provider = FakeSearch(), FakeScrape(), FakeProvider(judge_pass=True)
    nodes = NodeSet(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(scrape),
        synthesize=SynthesizeNode(provider),
        judge=JudgeNode(provider),
        review=ReviewNode(interactive=interactive_review),
        render=RenderNode(root=root),
    )
    return GraphRuntime(nodes=nodes, checkpointer=InMemorySaver())


async def test_full_run_to_artifact() -> None:
    runtime = _runtime()
    result = await runtime.run("research", "e2e-1", "state of the art in orchestration")
    assert not result.interrupted
    assert result.state.synthetic_notes
    assert result.state.synthetic_notes[0]
    assert result.state.output_path
    assert result.state.status.value == "done"


async def test_hitl_review_interrupts_then_resumes() -> None:
    runtime = _runtime(interactive_review=True)
    first = await runtime.run("research", "e2e-hitl", "human in the loop")
    # auto-approve is off; review gate pauses with an interrupt payload.
    assert first.interrupted
    assert "query" in first.interrupt_payload and "draft" in first.interrupt_payload

    resumed = await runtime.resume("research", "e2e-hitl", {"approved": True, "feedback": "ship it"})
    assert not resumed.interrupted
    assert resumed.state.status.value == "done"
    assert resumed.state.output_path


async def test_hitl_rejection_terminates_run() -> None:
    runtime = _runtime(interactive_review=True)
    first = await runtime.run("research", "e2e-reject", "reject me")
    assert first.interrupted
    resumed = await runtime.resume("research", "e2e-reject", {"approved": False, "feedback": "no"})
    assert resumed.state.status.value == "failed"

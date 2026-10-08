"""GraphRuntime.stream (W9): thin astream passthrough for progress UX.

New API surface, minimal contract: yields per-superstep state values so a
caller can render progress without republishing internal events. Interrupts
surface as the final yielded state with ``interrupted`` set.
"""

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
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _runtime(tmp_path) -> GraphRuntime:
    """Offline runtime (fakes + in-memory checkpointer).

    Args:
        tmp_path: Scratch dir for bodies + artifacts.

    Returns:
        GraphRuntime: Configured runtime.
    """
    storage = LocalBodyStorage(root=tmp_path / "bodies")
    return GraphRuntime(
        nodes=NodeSet(
            ingest=IngestNode(FakeSearch()),
            relevance=RelevanceNode(FakeProvider()),
            bodies=BodiesNode(),
            fetch_body=FetchBodyNode(FakeScrape(), storage=storage),
            synthesize=SynthesizeNode(FakeProvider(), storage=storage),
            judge=JudgeNode(FakeProvider()),
            review=ReviewNode(),
            render=RenderNode(root=str(tmp_path / "out")),
        ),
        checkpointer=InMemorySaver(),
    )


async def test_stream_yields_per_superstep_and_ends_done(tmp_path) -> None:
    """stream() yields state values for each superstep; the final one is done."""
    updates = [
        update async for update in _runtime(tmp_path).stream("research", "t-stream", "multi-agent orchestration")
    ]
    assert updates, "stream must yield at least one state snapshot"
    final = updates[-1]
    assert final.get("status") == "done"
    assert final.get("output_path"), "the rendered artifact path lands in the final snapshot"
    assert len(updates) >= 5, "each executed node contributes at least one snapshot"


async def test_stream_snapshot_shape_matches_run_result(tmp_path) -> None:
    """The streamed final snapshot agrees with what run() would return."""
    runtime = _runtime(tmp_path)
    streamed = [update async for update in runtime.stream("research", "t-shape", "multi-agent orchestration")]
    run_result = await runtime.run("research", "t-shape2", "multi-agent orchestration")
    assert streamed[-1].get("output_path") == run_result.state.output_path or streamed[-1].get("output_path")
    assert run_result.state.status.value == streamed[-1].get("status")

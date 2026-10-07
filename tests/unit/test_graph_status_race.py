"""Unit tests: concurrent error-handler writes must not crash the graph.

Regression test for the deep-workflow ``InvalidUpdateError: At key 'status'``
seen when two branches fail in the same step: the error handler writes
``status`` + ``judge_critique`` from parallel nodes, and LastValue channels
reject that. Last-writer-wins reducers make the failure path deterministic.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph", reason="graph runtime not in minimal env")

from langgraph.graph import END, START, StateGraph  # noqa: E402

from knowledge_engine.schemas.extraction import GraphState, GraphStatus  # noqa: E402


def _failing_branch(critique: str):
    """Build a branch node failing like the graph error handler does."""

    async def _node(state: GraphState) -> dict:
        return {"status": GraphStatus.FAILED.value, "judge_critique": critique}

    return _node


async def test_concurrent_status_writes_do_not_raise() -> None:
    """Two parallel FAILED writes merge instead of raising InvalidUpdateError."""
    graph = StateGraph(GraphState)
    graph.add_node("branch_a", _failing_branch("boom-a"))
    graph.add_node("branch_b", _failing_branch("boom-b"))
    graph.add_edge(START, "branch_a")
    graph.add_edge(START, "branch_b")
    graph.add_edge("branch_a", END)
    graph.add_edge("branch_b", END)
    app = graph.compile()
    final = await app.ainvoke(GraphState(thread_id="t", query="q"))
    assert final["status"] == GraphStatus.FAILED.value
    assert final["judge_critique"] in {"boom-a", "boom-b"}

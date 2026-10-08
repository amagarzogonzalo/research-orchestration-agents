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


async def test_concurrent_status_writes_on_workflow_state_do_not_raise() -> None:
    """The REAL graph state (WorkflowState) must merge concurrent failure writes.

    The production graphs compile over WorkflowState; only GraphState's
    reducers were covered before, so two branches failing in the same
    superstep still crashed the run with InvalidUpdateError.
    """
    from knowledge_engine.orchestration.state import WorkflowState

    async def _sel(state: dict) -> dict:
        return {"status": GraphStatus.FAILED.value, "judge_critique": "boom"}

    graph = StateGraph(WorkflowState)
    graph.add_node("branch_a", _sel)
    graph.add_node("branch_b", _sel)
    graph.add_node("branch_c", _sel)
    graph.add_node("branch_d", _sel)
    for name in ("branch_a", "branch_b", "branch_c", "branch_d"):
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    app = graph.compile()
    final = await app.ainvoke({"thread_id": "t", "query": "q", "status": "running"})
    assert final["status"] == GraphStatus.FAILED.value

"""Integration tests: FileCheckpointer persists + resumes LangGraph state.

Verifies the files-only checkpointer supports a simple graph round-trip:
aput/aget_tuple, pending writes, listing, and thread deletion. Proves
durability/HITL works with plain JSON files (no SQLite/Postgres).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver  # noqa: F401 - parity reference
from langgraph.graph import END, START, StateGraph

from knowledge_engine.store.file_checkpointer import FileCheckpointer


class S(TypedDict, total=False):
    count: Annotated[int, add]
    label: str


def _inc(state: S) -> S:
    return {"count": 1, "label": state.get("label", "") + "a"}


async def test_checkpointer_roundtrip(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "t1"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    snapshot = await app.aget_state(config)
    assert snapshot.values.get("label") == "a"
    assert list((tmp_path / "ckpt" / "t1").glob("*.json"))


async def test_checkpointer_rewrite_same_thread(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "t2"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    await app.ainvoke({"count": 0, "label": "x"}, config)
    snapshot = await app.aget_state(config)
    assert "x" in snapshot.values.get("label", "")


async def test_checkpointer_isolates_threads(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    await app.ainvoke({"count": 0, "label": "a"}, {"configurable": {"thread_id": "tA"}})
    await app.ainvoke({"count": 0, "label": "b"}, {"configurable": {"thread_id": "tB"}})
    a = await app.aget_state({"configurable": {"thread_id": "tA"}})
    b = await app.aget_state({"configurable": {"thread_id": "tB"}})
    assert "a" in a.values.get("label", "")
    assert "b" in b.values.get("label", "")


async def test_checkpointer_delete_thread(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "tD"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    await cp.adelete_thread("tD")
    assert not (tmp_path / "ckpt" / "tD").exists()


async def test_checkpointer_history_lists_checkpoints(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "tH"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    history = [c async for c in cp.alist(config)]
    assert len(history) >= 1


async def test_checkpointer_locks_are_per_thread(tmp_path) -> None:
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    assert await cp._lock_for("t1") is await cp._lock_for("t1")
    assert await cp._lock_for("t1") is not await cp._lock_for("t2")


async def test_checkpointer_parallel_threads_stay_isolated(tmp_path) -> None:
    """N concurrent runs on distinct threads share one checkpointer safely."""
    import asyncio

    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    tags = ("a", "b", "c", "d")
    await asyncio.gather(
        *(app.ainvoke({"count": 0, "label": tag}, {"configurable": {"thread_id": f"tp-{tag}"}}) for tag in tags)
    )
    for tag in tags:
        snap = await app.aget_state({"configurable": {"thread_id": f"tp-{tag}"}})
        assert tag in snap.values.get("label", ""), f"thread tp-{tag} lost its state"

"""FileCheckpointer matrix (W7): the paths the official conformance suite would cover.

Covers the four riskiest contracts of the custom saver: time-travel by
explicit ``checkpoint_id`` (a broken specific-id lookup silently corrupts
delta-channel state upstream), ``alist`` ``before``/``limit`` semantics,
pending-writes recovery across a crashed superstep, and the
lexicographic-time-sortable id ordering assumption that ``aget_tuple``'s
"latest" heuristic rests on.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from knowledge_engine.store.file_checkpointer import FileCheckpointer


class S(TypedDict, total=False):
    count: Annotated[int, add]
    label: str


def _inc(state: S) -> S:
    return {"count": 1, "label": state.get("label", "") + "a"}


def _dec(state: S) -> S:
    return {"count": -1, "label": state.get("label", "") + "d"}


async def test_time_travel_by_explicit_checkpoint_id(tmp_path) -> None:
    """aget_tuple with an explicit checkpoint_id returns THAT checkpoint, not the latest."""
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "tt"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    latest = await cp.aget_tuple(config)
    assert latest is not None
    # Time-travel: resolve the FIRST (root) checkpoint explicitly.
    history = [item async for item in cp.alist(config)]
    root = history[-1]
    assert root is not None
    specific = await cp.aget_tuple(root.config)
    assert specific is not None
    assert specific.checkpoint["id"] == root.checkpoint["id"]
    assert specific.checkpoint["id"] != latest.checkpoint["id"], "root != latest on a multi-step run"


async def test_alist_respects_before_and_limit(tmp_path) -> None:
    """alist returns newest-first, honors ``before`` and ``limit``."""
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "tl"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    all_tuples = [item async for item in cp.alist(config)]
    limited = [item async for item in cp.alist(config, limit=1)]
    assert len(limited) == 1
    assert limited[0].checkpoint["id"] == all_tuples[0].checkpoint["id"], "limit returns newest-first"
    oldest = all_tuples[-1]
    before_oldest = [item async for item in cp.alist(config, before=oldest.config)]
    assert before_oldest == [], "before the oldest there is nothing"
    after_first = [item async for item in cp.alist(config, before=all_tuples[0].config)]
    assert len(after_first) == len(all_tuples) - 1, "before the newest excludes exactly the newest"


async def test_pending_writes_survive_a_crashed_superstep(tmp_path) -> None:
    """Writes from a successful node in a failing superstep are recoverable.

    Simulates the crash: put_writes for task A, no checkpoint commit, then
    aget_tuple must surface A's writes as pending_writes.
    """
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_edge(START, "inc").add_edge("inc", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "tw"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    # Take a real checkpoint (as the loop would), then simulate a sibling
    # task's write landing after it, mid-superstep.
    current = await cp.aget_tuple(config)
    assert current is not None
    next_config = {
        "configurable": {
            "thread_id": "tw",
            "checkpoint_id": current.checkpoint["id"],
        }
    }
    await cp.aput_writes(next_config, [("count", 5)], task_id="crashed-task")
    after = await cp.aget_tuple(config)
    assert after is not None
    pending = [w for w in (after.pending_writes or []) if w[0] == "crashed-task"]
    assert pending and pending[0][1] == "count", "pending write from the crashed task survives"


async def test_checkpoint_ids_are_time_sortable(tmp_path) -> None:
    """Sequential checkpoints sort lexicographically newest-first (pins the aget_tuple heuristic)."""
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("inc", _inc).add_node("dec", _dec)
    g.add_edge(START, "inc").add_edge("inc", "dec").add_edge("dec", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "ts"}}
    await app.ainvoke({"count": 0, "label": ""}, config)
    ids = [item.checkpoint["id"] for item in ([c async for c in cp.alist(config)])]
    assert ids == sorted(ids, reverse=True), "newest-first = lexicographic desc; upstream ids must stay time-sortable"
    assert len(ids) >= 3, "a two-node run checkpoints at least 3 supersteps"

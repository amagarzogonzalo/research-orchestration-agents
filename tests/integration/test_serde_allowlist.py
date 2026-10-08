"""Serde allow-list (W10 follow-up): channel types never warn as unregistered.

LangGraph plans to block unrecognized msgpack ext types; our channel models
(``SourceRecord``/``BodyRef`` et al.) are allow-listed explicitly in both
savers so the warning is gone today and a hard block can never appear on an
upgrade.
"""

from __future__ import annotations

import logging
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.file_checkpointer import FileCheckpointer


class S(TypedDict, total=False):
    count: Annotated[int, add]
    record: SourceRecord | None


def _stash(state: S) -> S:
    """Write a typed record into the channel (serde roundtrip subject)."""
    return {
        "count": 1,
        "record": SourceRecord.model_validate(
            {"id": "openalex:W1", "title": "Paper One", "source": SearchSource.OPENALEX.value, "doi": "10.1000/x"}
        ),
    }


async def test_roundtrip_of_channel_records_emits_no_unregistered_warning(tmp_path, caplog) -> None:
    """A full graph run with domain models in channels logs no serde warning."""
    cp = FileCheckpointer(str(tmp_path / "ckpt"))
    g = StateGraph(S).add_node("stash", _stash).add_edge(START, "stash").add_edge("stash", END)
    app = g.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "t-serde"}}
    with caplog.at_level(logging.WARNING):
        await app.ainvoke({"count": 0, "record": None}, config)
        snapshot = await app.aget_state(config)
    assert snapshot.values.get("record") is not None, "SourceRecord survives the checkpoint roundtrip"
    warnings = [r.getMessage() for r in caplog.records if "unregistered" in r.getMessage().lower()]
    assert warnings == [], f"serde allow-list leaked: {warnings}"


async def test_checkpoint_factory_memory_saver_is_allowlisted(caplog) -> None:
    """build_checkpointer('memory') carries the same allow-listed serde."""
    from knowledge_engine.orchestration.checkpoint import build_checkpointer

    saver = build_checkpointer("memory")
    assert saver.serde is not None
    assert getattr(saver.serde, "_allowed_msgpack_modules", None), "memory saver must carry the allow-list"

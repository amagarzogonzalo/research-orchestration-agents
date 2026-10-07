"""Unit tests: workflow state snapshotting (ADR 0008)."""

from __future__ import annotations

from knowledge_engine.orchestration.state import to_graph_state
from knowledge_engine.schemas.extraction import GraphStatus
from knowledge_engine.schemas.source import DocumentBody, SearchSource, SourceRecord


def test_to_graph_state_roundtrips_channel_state() -> None:
    src = SourceRecord(id="openalex:W1", title="T", source=SearchSource.OPENALEX)
    body = DocumentBody(url="https://x.org", title="B", markdown="md")
    state = {
        "thread_id": "t1",
        "workflow": "research",
        "query": "multi-agent",
        "sources": [src],
        "filtered": [src],
        "bodies": [body],
        "notes": ["note1", "note2"],
        "judge_score": 0.8,
        "judge_critique": "tighten",
        "iterations": 2,
        "status": "running",
    }
    gs = to_graph_state(state)
    assert gs.thread_id == "t1"
    assert gs.query == "multi-agent"
    assert gs.synthetic_notes == ["note1", "note2"]
    assert gs.judge_score == 0.8
    assert gs.iterations == 2
    assert gs.status is GraphStatus.RUNNING


def test_to_graph_state_defaults_for_sparse_state() -> None:
    gs = to_graph_state({"thread_id": "t9"})
    assert gs.query == ""
    assert gs.sources == []
    assert gs.notes if hasattr(gs, "notes") else gs.synthetic_notes == []
    assert gs.status is GraphStatus.RUNNING
    assert gs.iterations == 0

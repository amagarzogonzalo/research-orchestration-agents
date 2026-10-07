"""Integration tests: studio entrypoint imports cleanly with compiled graphs."""

from __future__ import annotations


def test_studio_graphs_compile_with_expected_nodes() -> None:
    from knowledge_engine import studio

    assert set(studio.graph_deep.get_graph().nodes) >= {"intent", "decompose", "branch", "aggregate", "critic"}
    assert set(studio.graph_research.get_graph().nodes) >= {"ingest", "judge", "review", "render"}

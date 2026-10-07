"""Unit tests: graph.svg generator (offline, no browser/LLM)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from knowledge_engine.scripts.generate_graph_svg import (
    NODES,
    WORKFLOW_SPECS,
    WORKFLOWS,
    render_professional_svg,
    render_svg,
    render_workflow_svg,
)


def test_render_svg_is_well_formed_xml() -> None:
    ET.fromstring(render_svg())


def test_render_svg_contains_all_nodes_and_stages() -> None:
    svg = render_svg()
    for _, lines, _, _, _ in NODES:
        assert lines[0] in svg
    for stage in ("Data", "Gen", "Review", "Final"):
        assert stage in svg


def test_render_svg_marks_the_optimizer_loop() -> None:
    svg = render_svg()
    assert "re-iterate" in svg
    assert "class='cycle'" in svg or 'class="cycle"' in svg


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_render_workflow_svg_is_well_formed_xml(workflow: str) -> None:
    ET.fromstring(render_workflow_svg(workflow))


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_render_workflow_svg_matches_compiled_graph(workflow: str) -> None:
    """Every node/edge of the live compiled graph appears in its SVG."""
    from knowledge_engine.scripts.generate_graph_svg import compiled_graph

    svg = render_workflow_svg(workflow)
    graph = compiled_graph(workflow).get_graph()
    node_ids = {node.id if hasattr(node, "id") else str(node) for node in graph.nodes}
    assert node_ids, f"compiled graph has no nodes: {workflow}"
    for node_id in node_ids:
        assert node_id in svg, f"node {node_id!r} missing from {workflow} SVG"
    for edge in graph.edges:
        assert edge.source in svg, f"edge source {edge.source!r} missing from {workflow} SVG"
        assert edge.target in svg, f"edge target {edge.target!r} missing from {workflow} SVG"


def test_workflows_cover_all_executed_graphs() -> None:
    assert set(WORKFLOWS) == {"research", "qa", "summary", "deep"}


# ---------------------------------------------------------------------------
# Professional diagrams (docs/diagrams/): curated topology pinned to builders.
# ---------------------------------------------------------------------------

_EXPECTED_NODES: dict[str, set[str]] = {
    "research": {
        "__start__",
        "ingest",
        "relevance",
        "bodies",
        "fetch_body",
        "synthesize",
        "judge",
        "review",
        "render",
        "__end__",
    },
    "qa": {"__start__", "ingest", "relevance", "synthesize", "render", "__end__"},
    "summary": {"__start__", "ingest", "bodies", "fetch_body", "synthesize", "render", "__end__"},
    "deep": {
        "__start__",
        "intent",
        "decompose",
        "branch",
        "aggregate",
        "critic",
        "judge",
        "synthesize",
        "review",
        "render",
        "__end__",
    },
}

# Edges LangGraph's get_graph() cannot see (Send/Command dynamic routes) that
# the curated diagrams must show — the whole point of curating.
_EXPECTED_EDGES: dict[str, set[tuple[str, str]]] = {
    "research": {
        ("bodies", "fetch_body"),
        ("fetch_body", "synthesize"),
        ("synthesize", "judge"),
        ("judge", "synthesize"),
        ("judge", "review"),
        ("review", "render"),
        ("review", "__end__"),
        ("render", "__end__"),
    },
    "qa": {
        ("ingest", "relevance"),
        ("relevance", "synthesize"),
        ("synthesize", "render"),
        ("render", "__end__"),
    },
    "summary": {
        ("ingest", "bodies"),
        ("bodies", "fetch_body"),
        ("fetch_body", "synthesize"),
        ("synthesize", "render"),
        ("render", "__end__"),
    },
    "deep": {
        ("intent", "decompose"),
        ("intent", "synthesize"),
        ("decompose", "branch"),
        ("branch", "aggregate"),
        ("aggregate", "decompose"),
        ("aggregate", "critic"),
        ("critic", "judge"),
        ("judge", "synthesize"),
        ("synthesize", "judge"),
        ("judge", "review"),
        ("review", "render"),
        ("review", "__end__"),
        ("render", "__end__"),
    },
}


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_professional_svg_is_well_formed_xml(workflow: str) -> None:
    ET.fromstring(render_professional_svg(workflow))


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_professional_spec_matches_expected_topology(workflow: str) -> None:
    """Spec nodes/edges equal the builder-defined topology (fails on drift)."""
    spec = WORKFLOW_SPECS[workflow]
    assert {n.node_id for n in spec.nodes} == _EXPECTED_NODES[workflow]
    assert {(e.src, e.dst) for e in spec.edges} >= _EXPECTED_EDGES[workflow]


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_professional_spec_node_ids_exist_in_compiled_graph(workflow: str) -> None:
    """Every curated node id is a real node of the compiled workflow graph."""
    from knowledge_engine.scripts.generate_graph_svg import compiled_graph

    graph = compiled_graph(workflow).get_graph()
    live = {node.id if hasattr(node, "id") else str(node) for node in graph.nodes}
    for node in WORKFLOW_SPECS[workflow].nodes:
        assert node.node_id in live, f"{node.node_id!r} not in compiled {workflow} graph"


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_professional_svg_shows_send_and_loop_routing(workflow: str) -> None:
    svg = render_professional_svg(workflow)
    assert "marker-end" in svg  # directional arrowheads
    assert "Nodes" in svg and "Edges" in svg  # legend
    if workflow in ("research", "summary", "deep"):
        assert "Send ×N" in svg  # fan-out the auto-layout never labeled


def test_professional_svg_rejects_unknown_workflow() -> None:
    with pytest.raises(ValueError, match="unknown workflow"):
        render_professional_svg("nonexistent")


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_committed_diagrams_are_current(workflow: str) -> None:
    """Committed docs/diagrams SVGs match the renderer (refresh on change)."""
    from knowledge_engine.scripts.generate_graph_svg import DIAGRAM_DIR

    committed = (DIAGRAM_DIR / f"graph-{workflow}.svg").read_text(encoding="utf-8")
    assert committed == render_professional_svg(workflow)


def test_diagram_alias_matches_research() -> None:
    from knowledge_engine.scripts.generate_graph_svg import DIAGRAM_DIR

    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "diagrams").resolve() == DIAGRAM_DIR.resolve()
    assert (DIAGRAM_DIR / "graph.svg").read_text(encoding="utf-8") == render_professional_svg("research")

"""Unit tests: graph.svg generator (offline, no browser/LLM)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from knowledge_engine.scripts.generate_graph_svg import NODES, WORKFLOWS, render_svg, render_workflow_svg


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

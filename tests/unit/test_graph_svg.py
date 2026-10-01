"""Unit tests: graph.svg generator (offline, no browser/LLM)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from knowledge_engine.scripts.generate_graph_svg import NODES, render_svg


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

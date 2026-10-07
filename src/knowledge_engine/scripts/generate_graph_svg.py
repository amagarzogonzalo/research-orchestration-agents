"""Generate polished SVG diagrams of the Knowledge Engine LangGraph pipelines.

Reads (a hand-curated mirror of) the topology from
:mod:`knowledge_engine.orchestration.graphs` and renders ``graph.svg`` at the
repo root — nodes grouped by stage, color-coded, with conditional-routing
labels and the evaluator-optimizer loop highlighted. Mirrors the digest-agent
``gen_graph_svg`` tool.

Run:  python -m knowledge_engine.scripts.generate_graph_svg
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_engine.core.ports import ProviderPort, ScrapePort, SearchPort
from knowledge_engine.orchestration import graphs
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import ChatRequest, ChatResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where the professional workflow diagrams live (see ``docs/diagrams/README.md``).
DIAGRAM_DIR = REPO_ROOT / "docs" / "diagrams"

WORKFLOWS: tuple[str, ...] = ("research", "qa", "summary", "deep")

# (node_id, label_lines, x, y, stage) — research workflow topology
NODES = [
    ("start", ["START"], 20, 30, "data"),
    ("ingest", ["ingest", "resolve sources"], 20, 95, "data"),
    ("relevance", ["relevance", "micro LLM filter"], 20, 160, "gen"),
    ("bodies", ["bodies", "Send fan-out"], 240, 40, "gen"),
    ("fetch", ["fetch_body", "scrape 1 source"], 240, 110, "gen"),
    ("synthesize", ["synthesize", "MACRO LLM"], 240, 180, "gen"),
    ("judge", ["judge", "LLM-as-a-judge"], 240, 250, "review"),
    ("review", ["review", "interrupt (HITL)"], 460, 60, "review"),
    ("render", ["render", "article + files"], 460, 130, "final"),
    ("end", ["END"], 460, 200, "final"),
]

# (from, to, label, cycle?)
EDGES = [
    ("start", "ingest", "", False),
    ("ingest", "relevance", "", False),
    ("relevance", "bodies", "", False),
    ("bodies", "fetch", "Send[source]", False),
    ("bodies", "synthesize", "empty → join", False),
    ("fetch", "synthesize", "", False),
    ("synthesize", "judge", "", False),
    ("judge", "synthesize", "re-iterate (≤N)", True),
    ("judge", "review", "pass", False),
    ("review", "render", "approved", False),
    ("review", "end", "rejected", False),
    ("render", "end", "", False),
]

STAGE = {
    "data": ("#e1effe", "#1d4ed8"),
    "gen": ("#e7f6ec", "#15803d"),
    "review": ("#fff4e5", "#b4530a"),
    "final": ("#f3e8fd", "#7e22ce"),
}

BW = 170
BH = 36


def _esc(text: str) -> str:
    """Esc.

    Args:
        text: Encoded text.

    Returns:
        str: Result text.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg() -> str:
    """Return the full SVG document as a string.

    Returns:
        str: Result text.
    """
    pos = {n[0]: (n[2], n[3]) for n in NODES}
    W, H = 660, 310
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}'>"]
    out.append(
        "<style>"
        " text{font-family:Inter,Helvetica,Arial,sans-serif;fill:#111827;font-size:11px}"
        " .sub{font-size:9px;fill:#374151}"
        " .lbl{font-size:9px;fill:#6b7280;font-style:italic}"
        " .edge{stroke:#9ca3af;stroke-width:1.3;fill:none}"
        " .cycle{stroke:#dc2626;stroke-width:1.6;stroke-dasharray:5,3}"
        " .legend{font-size:10px;fill:#111827}"
        "</style>"
    )
    # edges
    for frm, to, label, cycle in EDGES:
        x1, y1 = pos[frm]
        x2, y2 = pos[to]
        mid_x = (x1 + x2) / 2 if x1 != x2 else x1 + BW
        mid_y = (y1 + y2) / 2
        cls = "cycle" if cycle else "edge"
        start_x = x1 + BW if x1 < x2 else x1
        end_x = x2 if x1 < x2 else x2 + BW
        out.append(
            f"<path class='{cls}' d='M{start_x},{y1 + BH / 2} "
            f"C{mid_x},{y1 + BH / 2} {mid_x},{y2 + BH / 2} {end_x},{y2 + BH / 2}'/>"
        )
        if label:
            out.append(f"<text class='lbl' x='{mid_x + 6}' y='{mid_y}'>{_esc(label)}</text>")
    # nodes
    for _, lines, x, y, stage in NODES:
        fill, stroke = STAGE.get(stage, STAGE["data"])
        out.append(
            f"<rect x='{x}' y='{y}' width='{BW}' height='{BH}' rx='6' "
            f"fill='{fill}' stroke='{stroke}' stroke-width='1.4'/>"
        )
        out.append(f"<text x='{x + 12}' y='{y + 14}' style='font-weight:600'>{_esc(lines[0])}</text>")
        for i, line in enumerate(lines[1:], start=1):
            out.append(f"<text class='sub' x='{x + 12}' y='{y + 14 + i * 11}'>{_esc(line)}</text>")
    # legend
    ly = H - 40
    for i, (name, (fill, stroke)) in enumerate(STAGE.items()):
        lx = 20 + i * 150
        out.append(f"<rect x='{lx}' y='{ly}' width='12' height='12' fill='{fill}' stroke='{stroke}'/>")
        out.append(f"<text class='legend' x='{lx + 18}' y='{ly + 11}'>{name.title()}</text>")
    out.append(
        f"<text class='legend' x='20' y='{H - 12}'>"
        "Knowledge Engine research workflow (StateGraph · Send fan-out · "
        "evaluator-optimizer · review interrupt)</text>"
    )
    out.append("</svg>\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Professional workflow diagrams (curated topology → docs/diagrams/).
# ---------------------------------------------------------------------------
# Rationale: LangGraph's ``get_graph()`` cannot render ``Send``/``Command``
# dynamic routes (the bodies fan-out, the empty-pool join, the judge revise
# loop), and the old auto-layout stacked every node in one column while
# listing nodes that are not even connected in that workflow. These specs are
# therefore curated from the builder functions in
# ``orchestration/graphs.py`` (``_build_research/_build_qa/_build_summary/
# _build_deep``) — the ground truth — and ``test_graph_svg.py`` pins them to
# it, so a topology change without a diagram refresh fails loudly.


@dataclass(frozen=True)
class DiagramNode:
    """One card on a professional workflow diagram.

    Attributes:
        node_id: Graph node id (must exist in the compiled workflow graph).
        title: Card headline (usually the node id, ``×N`` for fan-out workers).
        sub: One-line card caption describing what the node does.
        stage: Card accent color: ``data`` | ``gen`` | ``review`` | ``final`` | ``control``.
        col: Zero-based layout column (left → right = execution order).
        row: Zero-based layout row (fan-out workers sit on row 1).
        step: Happy-path order badge (``""`` hides it; used for START/END).
        ghost: Draw stacked ghost cards behind (parallel ``Send`` workers).
        detail: Expansion chips drawn in a dashed box under the card, e.g. the
            fixed leaf pipeline a ``branch`` worker runs (``()`` hides it).
    """

    node_id: str
    title: str
    sub: str
    stage: str
    col: int
    row: int
    step: str = ""
    ghost: bool = False
    detail: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagramEdge:
    """One arrow on a professional workflow diagram.

    Attributes:
        src: Source node id.
        dst: Target node id.
        label: Edge caption (``""`` hides it).
        kind: ``flow`` (sequential) | ``fanout`` (Send) | ``join`` (fan-in) |
            ``loop`` (revise/replan, red dashed) | ``alt`` (conditional branch).
        bottom: Route through the bottom lane (used when the direct path would
            cross another card, e.g. loops and long conditional jumps).
        lane: Bottom-lane slot for parallel loop lanes (0-based).
    """

    src: str
    dst: str
    label: str = ""
    kind: str = "flow"
    bottom: bool = False
    lane: int = 0


@dataclass(frozen=True)
class WorkflowDiagram:
    """Curated spec for one workflow's professional diagram.

    Attributes:
        slug: Workflow name (``research`` | ``qa`` | ``summary`` | ``deep``).
        title: Diagram headline.
        subtitle: One-line topology summary under the headline.
        source_ref: Builder reference (file + function) for traceability.
        lanes: Column captions, one per layout column.
        nodes: Node cards.
        edges: Arrows.
        note: Design-note bar under the diagram (``""`` hides it), e.g. the
            workers-are-not-subagents clarification.
    """

    slug: str
    title: str
    subtitle: str
    source_ref: str
    lanes: tuple[str, ...]
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]
    note: str = ""


WORKFLOW_SPECS: dict[str, WorkflowDiagram] = {
    "research": WorkflowDiagram(
        slug="research",
        title="Research workflow",
        subtitle="Default · search → filter → bounded scrape (16+4) → synthesize → judge → review",
        source_ref="orchestration/graphs.py :: _build_research",
        note=(
            "Deterministic: the graph calls tools on a fixed schedule — "
            "the LLM only synthesizes, it never chooses tools."
        ),
        lanes=("enter", "search", "filter", "fan-out", "synthesize", "assure", "review", "publish", "exit"),
        nodes=(
            DiagramNode("__start__", "START", "entry", "control", 0, 0),
            DiagramNode("ingest", "ingest", "federated search · OpenAlex, Crossref…", "data", 1, 0, "1"),
            DiagramNode("relevance", "relevance", "topicality filter · one batched LLM call", "gen", 2, 0, "2"),
            DiagramNode("bodies", "bodies", "router · bounded pool 16 scholarly + 4 web", "gen", 3, 0, "3"),
            DiagramNode("fetch_body", "fetch_body ×N", "scrape one source → Markdown", "gen", 3, 1, "4", True),
            DiagramNode("synthesize", "synthesize", "one Macro LLM draft from evidence", "gen", 4, 0, "5"),
            DiagramNode("judge", "judge", "LLM-as-judge · groundedness 0–1", "review", 5, 0, "6"),
            DiagramNode("review", "review", "HITL gate · auto-approve or interrupt", "review", 6, 0, "7"),
            DiagramNode("render", "render", "article.md · frontmatter + citations", "final", 7, 0, "8"),
            DiagramNode("__end__", "END", "exit", "control", 8, 0),
        ),
        edges=(
            DiagramEdge("__start__", "ingest"),
            DiagramEdge("ingest", "relevance", "sources"),
            DiagramEdge("relevance", "bodies", "filtered"),
            DiagramEdge("bodies", "fetch_body", "Send ×N · 16+4", "fanout"),
            DiagramEdge("bodies", "synthesize", "empty pool → join", "alt", True, 1),
            DiagramEdge("fetch_body", "synthesize", "bodies", "join"),
            DiagramEdge("synthesize", "judge", "draft"),
            DiagramEdge("judge", "synthesize", "revise · critique + budget left · ≤N", "loop", True, 0),
            DiagramEdge("judge", "review", "pass"),
            DiagramEdge("review", "render", "approved"),
            DiagramEdge("review", "__end__", "rejected", "alt", True, 2),
            DiagramEdge("render", "__end__", ""),
        ),
    ),
    "qa": WorkflowDiagram(
        slug="qa",
        title="QA workflow",
        subtitle="Fast + cheap · answers from abstracts · no scraping, no judge loop",
        source_ref="orchestration/graphs.py :: _build_qa",
        lanes=("enter", "search", "filter", "answer", "publish", "exit"),
        nodes=(
            DiagramNode("__start__", "START", "entry", "control", 0, 0),
            DiagramNode("ingest", "ingest", "federated search · OpenAlex, Crossref…", "data", 1, 0, "1"),
            DiagramNode("relevance", "relevance", "topicality filter · one batched LLM call", "gen", 2, 0, "2"),
            DiagramNode("synthesize", "synthesize", "answer from abstracts · no scrape", "gen", 3, 0, "3"),
            DiagramNode("render", "render", "article.md · frontmatter + citations", "final", 4, 0, "4"),
            DiagramNode("__end__", "END", "exit", "control", 5, 0),
        ),
        edges=(
            DiagramEdge("__start__", "ingest"),
            DiagramEdge("ingest", "relevance", "sources"),
            DiagramEdge("relevance", "synthesize", "abstracts"),
            DiagramEdge("synthesize", "render", "answer"),
            DiagramEdge("render", "__end__", ""),
        ),
    ),
    "summary": WorkflowDiagram(
        slug="summary",
        title="Summary workflow",
        subtitle="Summarize everything retrieved · no relevance gate, no judge",
        source_ref="orchestration/graphs.py :: _build_summary",
        lanes=("enter", "search", "fan-out", "synthesize", "publish", "exit"),
        nodes=(
            DiagramNode("__start__", "START", "entry", "control", 0, 0),
            DiagramNode("ingest", "ingest", "federated search · OpenAlex, Crossref…", "data", 1, 0, "1"),
            DiagramNode("bodies", "bodies", "router · bounded pool 16 scholarly + 4 web", "gen", 2, 0, "2"),
            DiagramNode("fetch_body", "fetch_body ×N", "scrape one source → Markdown", "gen", 2, 1, "3", True),
            DiagramNode("synthesize", "synthesize", "one Macro LLM draft from evidence", "gen", 3, 0, "4"),
            DiagramNode("render", "render", "article.md · frontmatter + citations", "final", 4, 0, "5"),
            DiagramNode("__end__", "END", "exit", "control", 5, 0),
        ),
        edges=(
            DiagramEdge("__start__", "ingest"),
            DiagramEdge("ingest", "bodies", "all sources"),
            DiagramEdge("bodies", "fetch_body", "Send ×N · 16+4", "fanout"),
            DiagramEdge("bodies", "synthesize", "empty pool → join", "alt", True, 0),
            DiagramEdge("fetch_body", "synthesize", "bodies", "join"),
            DiagramEdge("synthesize", "render", "draft"),
            DiagramEdge("render", "__end__", ""),
        ),
    ),
    "deep": WorkflowDiagram(
        slug="deep",
        title="Deep workflow",
        subtitle="Recursive planner · Send fan-out over branch workers · aggregate → critic → judge → render",
        source_ref="orchestration/graphs.py :: _build_deep",
        note="",
        lanes=(
            "enter",
            "route",
            "plan",
            "work",
            "merge",
            "critique",
            "assure",
            "decide",
            "publish",
            "exit",
        ),
        nodes=(
            DiagramNode("__start__", "START", "entry", "control", 0, 0),
            DiagramNode("intent", "intent", "router · deep question or cheap?", "gen", 1, 0, "1"),
            DiagramNode("decompose", "decompose", "planner · 7–10 sub-queries", "gen", 2, 0, "2"),
            DiagramNode(
                "branch",
                "branch ×N",
                "leaf worker per sub-query",
                "gen",
                3,
                0,
                "3",
                True,
                ("ingest", "relevance", "fetch ≤3", "synthesize"),
            ),
            DiagramNode("aggregate", "aggregate", "merge notes · flag gaps", "gen", 4, 0, "4"),
            DiagramNode("critic", "critic", "adversarial review of merged draft", "review", 5, 0, "5"),
            DiagramNode("judge", "judge", "LLM-as-judge · groundedness 0–1", "review", 6, 0, "6"),
            DiagramNode("synthesize", "synthesize", "Macro synthesis / refinement", "gen", 6, 1, "7"),
            DiagramNode("review", "review", "HITL gate · auto-approve or interrupt", "review", 7, 0, "8"),
            DiagramNode("render", "render", "article.md · frontmatter + citations", "final", 8, 0, "9"),
            DiagramNode("__end__", "END", "exit", "control", 9, 0),
        ),
        edges=(
            DiagramEdge("__start__", "intent"),
            DiagramEdge("intent", "decompose", "deep"),
            DiagramEdge("intent", "synthesize", "cheap → one pass", "alt", True, 0),
            DiagramEdge("decompose", "branch", "Send ×N", "fanout"),
            DiagramEdge("decompose", "judge", "empty plan → fail-safe", "alt", True, 1),
            DiagramEdge("branch", "aggregate", "notes"),
            DiagramEdge("aggregate", "decompose", "gaps remain · replan", "loop", True, 2),
            DiagramEdge("aggregate", "critic", "complete"),
            DiagramEdge("critic", "judge", "critique"),
            DiagramEdge("judge", "synthesize", "revise · ≤N", "loop"),
            DiagramEdge("synthesize", "judge", "re-check", "loop"),
            DiagramEdge("judge", "review", "pass"),
            DiagramEdge("synthesize", "render", "cheap → render", "alt", True, 3),
            DiagramEdge("review", "render", "approved"),
            DiagramEdge("review", "__end__", "rejected", "alt", True, 4),
            DiagramEdge("render", "__end__", ""),
        ),
    ),
}

# Professional renderer styling (print-safe, offline-safe: system fonts only).
_PRO_CARD_W = 184
_PRO_CARD_H = 60
_PRO_PITCH_X = 280
_PRO_PITCH_Y = 128
_PRO_MARGIN_X = 32
_PRO_HEADER_H = 104
_PRO_LANE_H = 26
_PRO_LANE_GAP = 30
_PRO_BOTTOM_GAP = 40
_PRO_LANE_STEP = 26
_PRO_LEGEND_H = 86

_PRO_STAGE: dict[str, tuple[str, str, str]] = {
    # stage -> (accent, border, badge)
    "data": ("#2563eb", "#93b4f5", "#1d4ed8"),
    "gen": ("#16a34a", "#86d3a3", "#15803d"),
    "review": ("#d97706", "#f2c078", "#b45309"),
    "final": ("#9333ea", "#c9a8f5", "#7e22ce"),
    "control": ("#334155", "#334155", "#0f172a"),
}

_PRO_EDGE: dict[str, tuple[str, float, str]] = {
    # kind -> (color, width, dasharray or "")
    "flow": ("#475569", 2.0, ""),
    "fanout": ("#2563eb", 2.0, ""),
    "join": ("#2563eb", 2.0, ""),
    "loop": ("#dc2626", 2.0, "7,4"),
    "alt": ("#b45309", 1.8, "5,4"),
}


def _pro_positions(spec: WorkflowDiagram) -> dict[str, tuple[int, int]]:
    """Map node ids to top-left card positions for a diagram spec.

    Args:
        spec: Workflow diagram spec.

    Returns:
        dict[str, tuple[int, int]]: Node id to ``(x, y)`` top-left corner.
    """
    origin_y = _PRO_HEADER_H + _PRO_LANE_H
    return {
        node.node_id: (_PRO_MARGIN_X + node.col * _PRO_PITCH_X, origin_y + node.row * _PRO_PITCH_Y)
        for node in spec.nodes
    }


def _pro_bottom_lane(spec: WorkflowDiagram, lane: int, positions: dict[str, tuple[int, int]]) -> int:
    """Compute the y of a bottom-lane routing slot shared by loop edges.

    Args:
        spec: Workflow diagram spec.
        lane: Zero-based lane slot.
        positions: Node top-left positions.

    Returns:
        int: Y coordinate of the horizontal lane run.
    """
    lowest = max(y for _, y in positions.values()) + _PRO_CARD_H
    return lowest + _PRO_BOTTOM_GAP + lane * _PRO_LANE_STEP


def _pro_markers() -> str:
    """Build SVG arrowhead marker definitions (one per edge color).

    Returns:
        str: ``<defs>`` block with markers.
    """
    colors = sorted({color for color, _, _ in _PRO_EDGE.values()})
    parts = ["<defs>"]
    for color in colors:
        key = color.lstrip("#")
        parts.append(
            f"<marker id='arr-{key}' viewBox='0 0 10 10' refX='8' refY='5' "
            f"markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
            f"<path d='M0,0 L10,5 L0,10 z' fill='{color}'/></marker>"
        )
    parts.append("</defs>")
    return "\n".join(parts)


def _pro_label_pill(x: float, y: float, label: str) -> str:
    """Render an edge label as a white pill with a thin gray border.

    Args:
        x: Pill center x.
        y: Pill center y.
        label: Label text.

    Returns:
        str: SVG ``<g>`` for the pill.
    """
    width = max(24, len(label) * 6.4 + 18)
    return (
        f"<g><rect x='{x - width / 2:.1f}' y='{y - 10:.1f}' width='{width:.1f}' height='20' rx='10' "
        f"fill='#ffffff' stroke='#cbd5e1' stroke-width='1'/>"
        f"<text x='{x:.1f}' y='{y + 3.5:.1f}' text-anchor='middle' "
        f"font-size='10' fill='#334155' font-style='italic'>{_esc(label)}</text></g>"
    )


def _pro_node_card(x: int, y: int, node: DiagramNode) -> str:
    """Render one node card (pill for START/END, badged card otherwise).

    Args:
        x: Card left.
        y: Card top.
        node: Node spec.

    Returns:
        str: SVG fragment for the card.
    """
    accent, border, badge = _PRO_STAGE.get(node.stage, _PRO_STAGE["data"])
    parts: list[str] = []
    if node.stage == "control":
        parts.append(
            f"<rect x='{x}' y='{y + 8}' width='{_PRO_CARD_W}' height='{_PRO_CARD_H - 16}' rx='22' "
            f"fill='#0f172a' stroke='#0f172a'/>"
        )
        parts.append(
            f"<text x='{x + _PRO_CARD_W / 2}' y='{y + 28}' text-anchor='middle' "
            f"font-size='12.5' font-weight='700' fill='#f8fafc'>{_esc(node.title)}</text>"
        )
        parts.append(
            f"<text x='{x + _PRO_CARD_W / 2}' y='{y + 43}' text-anchor='middle' "
            f"font-size='10' fill='#94a3b8'>{_esc(node.sub)}</text>"
        )
        return "\n".join(parts)
    if node.ghost:
        for dx, dy in ((12, 12), (6, 6)):
            parts.append(
                f"<rect x='{x + dx}' y='{y + dy}' width='{_PRO_CARD_W}' height='{_PRO_CARD_H}' rx='10' "
                f"fill='#f1f5f9' stroke='#cbd5e1' stroke-width='1.2'/>"
            )
    parts.append(
        f"<rect x='{x}' y='{y}' width='{_PRO_CARD_W}' height='{_PRO_CARD_H}' rx='10' "
        f"fill='#ffffff' stroke='{border}' stroke-width='1.5'/>"
    )
    parts.append(f"<rect x='{x}' y='{y}' width='5' height='{_PRO_CARD_H}' rx='2.5' fill='{accent}'/>")
    parts.append(
        f"<text x='{x + 16}' y='{y + 24}' font-size='12.5' font-weight='700' fill='#0f172a'>{_esc(node.title)}</text>"
    )
    parts.append(f"<text x='{x + 16}' y='{y + 42}' font-size='10' fill='#475569'>{_esc(node.sub)}</text>")
    if node.step:
        parts.append(f"<circle cx='{x + 18}' cy='{y}' r='11' fill='{badge}' stroke='#ffffff' stroke-width='2'/>")
        parts.append(
            f"<text x='{x + 18}' y='{y + 4}' text-anchor='middle' "
            f"font-size='10.5' font-weight='700' fill='#ffffff'>{_esc(node.step)}</text>"
        )
    return "\n".join(parts)


def _pro_detail_box(x: int, y: int, node: DiagramNode) -> str:
    """Render the expansion inset for a worker card (dashed box + chips).

    Args:
        x: Parent card left.
        y: Parent card top.
        node: Node spec carrying ``detail`` chip labels.

    Returns:
        str: SVG fragment for the dotted connector and dashed inset box.
    """
    box_y = y + _PRO_CARD_H + 10
    rows = (len(node.detail) + 1) // 2
    box_h = 8 + 14 + 6 + rows * 24
    chip_w = (_PRO_CARD_W - 16 - 6) // 2
    parts = [
        f"<line x1='{x + _PRO_CARD_W / 2}' y1='{y + _PRO_CARD_H}' x2='{x + _PRO_CARD_W / 2}' "
        f"y2='{box_y}' stroke='#94a3b8' stroke-width='1.4' stroke-dasharray='3,3'/>",
        f"<rect x='{x}' y='{box_y}' width='{_PRO_CARD_W}' height='{box_h}' rx='10' "
        f"fill='#f8fafc' stroke='#94a3b8' stroke-width='1.2' stroke-dasharray='6,4'/>",
        f"<text x='{x + 10}' y='{box_y + 16}' font-size='9.5' font-style='italic' "
        f"fill='#64748b'>each branch runs:</text>",
    ]
    for i, chip in enumerate(node.detail):
        cx = x + 8 + (i % 2) * (chip_w + 6)
        cy = box_y + 24 + (i // 2) * 24
        parts.append(
            f"<rect x='{cx}' y='{cy}' width='{chip_w}' height='18' rx='9' "
            f"fill='#ffffff' stroke='#86d3a3' stroke-width='1.2'/>"
        )
        parts.append(
            f"<text x='{cx + chip_w / 2}' y='{cy + 13}' text-anchor='middle' "
            f"font-size='9' fill='#15803d'>{_esc(chip)}</text>"
        )
    return "\n".join(parts)


def _pro_edge_path(
    spec: WorkflowDiagram,
    edge: DiagramEdge,
    positions: dict[str, tuple[int, int]],
    bottom_lanes: dict[int, int],
) -> tuple[str, tuple[float, float]]:
    """Build the SVG path for one edge plus its label anchor point.

    Args:
        spec: Workflow diagram spec (node rows/cols drive routing).
        edge: Edge spec.
        positions: Node top-left positions.
        bottom_lanes: Lane index to y coordinate for bottom-routed edges.

    Returns:
        tuple[str, tuple[float, float]]: Path ``d`` attribute and label ``(x, y)``.
    """
    color, width, dash = _PRO_EDGE.get(edge.kind, _PRO_EDGE["flow"])
    marker = f"marker-end='url(#arr-{color.lstrip('#')})'"
    dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
    x1, y1 = positions[edge.src]
    x2, y2 = positions[edge.dst]
    n1 = next(n for n in spec.nodes if n.node_id == edge.src)
    n2 = next(n for n in spec.nodes if n.node_id == edge.dst)
    sx: float = 0.0
    sy: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    label_pos: tuple[float, float] = (0.0, 0.0)

    if edge.bottom:
        lane_y = bottom_lanes[edge.lane]
        sx, sy = x1 + _PRO_CARD_W / 2, y1 + _PRO_CARD_H
        dx, dy = x2 + _PRO_CARD_W / 2, y2 + _PRO_CARD_H
        if abs(dx - sx) < 1:
            dx += 24
        r = 12
        sign = 1 if dx >= sx else -1
        d = (
            f"M{sx:.1f},{sy:.1f} L{sx:.1f},{lane_y - r:.1f} "
            f"Q{sx:.1f},{lane_y:.1f} {sx + sign * r:.1f},{lane_y:.1f} "
            f"L{dx - sign * r:.1f},{lane_y:.1f} "
            f"Q{dx:.1f},{lane_y:.1f} {dx:.1f},{lane_y - r:.1f} L{dx:.1f},{dy:.1f}"
        )
        label_pos = ((sx + dx) / 2, lane_y - 13)
    elif n1.col == n2.col and n1.row != n2.row:
        # Vertical adjacency (bodies → fetch_body, judge ⇄ synthesize).
        # Down-pill goes left, up-pill goes right so paired loop edges
        # (judge ⇄ synthesize) never share a label slot.
        downward = n2.row > n1.row
        half = len(edge.label) * 3.2 + 9
        sx = x1 + _PRO_CARD_W / 2
        sy = y1 + _PRO_CARD_H if downward else y1
        dy = y2 if downward else y2 + _PRO_CARD_H
        off = 46 if edge.kind == "loop" and not downward else 30
        d = f"M{sx:.1f},{sy:.1f} C{sx + off:.1f},{sy:.1f} {sx + off:.1f},{dy:.1f} {sx:.1f},{dy:.1f}"
        if downward:
            label_pos = (sx - half - 14, (sy + dy) / 2)
        else:
            label_pos = (sx + off + half + 14, (sy + dy) / 2)
    elif abs((y1 + _PRO_CARD_H / 2) - (y2 + _PRO_CARD_H / 2)) > 40:
        # Diagonal fan-in (fetch_body row 1 → synthesize row 0): the straight
        # midpoint sits on the card band, so tuck the pill inside the
        # fan-out triangle instead.
        sx, sy = x1 + _PRO_CARD_W, y1 + _PRO_CARD_H / 2
        dx, dy = x2, y2 + _PRO_CARD_H / 2
        mx = (sx + dx) / 2
        d = f"M{sx:.1f},{sy:.1f} C{mx:.1f},{sy:.1f} {mx:.1f},{dy:.1f} {dx:.1f},{dy:.1f}"
        label_pos = (mx - 40, (sy + dy) / 2 + 16)
    else:
        # Same-row (or diagonal) forward hop.
        sx, sy = x1 + _PRO_CARD_W, y1 + _PRO_CARD_H / 2
        dx, dy = x2, y2 + _PRO_CARD_H / 2
        mx = (sx + dx) / 2
        d = f"M{sx:.1f},{sy:.1f} C{mx:.1f},{sy:.1f} {mx:.1f},{dy:.1f} {dx:.1f},{dy:.1f}"
        label_pos = (mx, min(sy, dy) - 12 if abs(sy - dy) > 40 else sy - 14)
    path = f"<path d='{d}' fill='none' stroke='{color}' stroke-width='{width}'{dash_attr} {marker}/>"
    return path, label_pos


def render_professional_svg(workflow: str) -> str:
    """Render a professional layered SVG diagram for one workflow.

    Layout is deterministic: columns run left → right in execution order,
    ``Send`` fan-out workers stack on row 1 with ghost cards, revise/replan
    loops route through dashed red bottom lanes, and conditional branches use
    amber dashes. Only nodes reachable in the workflow appear (unlike the
    legacy auto-layout, which listed every registered node).

    Args:
        workflow: One of ``research``, ``qa``, ``summary``, ``deep``.

    Returns:
        str: SVG document.

    Raises:
        ValueError: When the workflow name is unknown.
    """
    if workflow not in WORKFLOW_SPECS:
        raise ValueError(f"unknown workflow: {workflow!r}")
    spec = WORKFLOW_SPECS[workflow]
    positions = _pro_positions(spec)
    max_col = max(node.col for node in spec.nodes)
    max_row = max(node.row for node in spec.nodes)
    width = _PRO_MARGIN_X * 2 + max_col * _PRO_PITCH_X + _PRO_CARD_W
    rows_bottom = _PRO_HEADER_H + _PRO_LANE_H + max_row * _PRO_PITCH_Y + _PRO_CARD_H
    lanes_needed = sorted({edge.lane for edge in spec.edges if edge.bottom})
    bottom_lanes = {lane: rows_bottom + _PRO_BOTTOM_GAP + i * _PRO_LANE_STEP for i, lane in enumerate(lanes_needed)}
    flow_bottom = (max(bottom_lanes.values()) + 30) if bottom_lanes else rows_bottom + 30
    if spec.note:
        note_y = flow_bottom
        legend_y = note_y + 30 + 24
    else:
        legend_y = flow_bottom
    height = legend_y + _PRO_LEGEND_H

    out = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}' role='img'>",
        f"<title>{_esc(spec.title)} — Knowledge Engine ({_esc(spec.slug)} workflow)</title>",
        "<style>"
        " text{font-family:Inter,Helvetica,Arial,sans-serif}"
        " .lane{font-size:10px;fill:#64748b;letter-spacing:1.5px}"
        " .legend{font-size:10.5px;fill:#334155}"
        " .caption{font-size:10px;fill:#64748b}"
        "</style>",
        _pro_markers(),
        f"<rect x='0' y='0' width='{width}' height='{height}' rx='12' fill='#ffffff' stroke='#e2e8f0'/>",
        # Header.
        f"<rect x='0' y='0' width='{width}' height='{_PRO_HEADER_H - 18}' rx='12' fill='#0f172a'/>",
        f"<rect x='0' y='{_PRO_HEADER_H - 30}' width='{width}' height='12' fill='#0f172a'/>",
        f"<text x='{_PRO_MARGIN_X}' y='36' font-size='18' font-weight='700' fill='#f8fafc'>{_esc(spec.title)}</text>",
        f"<text x='{_PRO_MARGIN_X}' y='60' font-size='12' fill='#94a3b8'>{_esc(spec.subtitle)}</text>",
        f"<text x='{width - _PRO_MARGIN_X}' y='36' font-size='10.5' fill='#94a3b8' text-anchor='end' "
        f"font-family='ui-monospace,Consolas,monospace'>{_esc(spec.source_ref)}</text>",
    ]
    # Lane captions.
    for col, lane in enumerate(spec.lanes):
        cx = _PRO_MARGIN_X + col * _PRO_PITCH_X + _PRO_CARD_W / 2
        out.append(
            f"<text class='lane' x='{cx:.1f}' y='{_PRO_HEADER_H + _PRO_LANE_H - 8}' "
            f"text-anchor='middle'>{_esc(lane.upper())}</text>"
        )
    # Edges under nodes; bottom lanes first.
    edge_frags: list[str] = []
    label_frags: list[str] = []
    for edge in sorted(spec.edges, key=lambda e: (not e.bottom, e.lane)):
        path, (lx, ly) = _pro_edge_path(spec, edge, positions, bottom_lanes)
        edge_frags.append(path)
        if edge.label:
            label_frags.append(_pro_label_pill(lx, ly, edge.label))
    out.extend(edge_frags)
    for node in spec.nodes:
        x, y = positions[node.node_id]
        out.append(_pro_node_card(x, y, node))
        if node.detail:
            out.append(_pro_detail_box(x, y, node))
    out.extend(label_frags)
    # Design-note bar.
    if spec.note:
        out.append(
            f"<rect x='{_PRO_MARGIN_X}' y='{note_y}' width='{width - 2 * _PRO_MARGIN_X}' "
            f"height='30' rx='8' fill='#f8fafc' stroke='#e2e8f0'/>"
        )
        out.append(
            f"<circle cx='{_PRO_MARGIN_X + 18}' cy='{note_y + 15}' r='9' "
            f"fill='none' stroke='#64748b' stroke-width='1.4'/>"
        )
        out.append(
            f"<text x='{_PRO_MARGIN_X + 18}' y='{note_y + 19}' text-anchor='middle' "
            f"font-size='10' font-style='italic' font-weight='700' fill='#64748b'>i</text>"
        )
        out.append(
            f"<text x='{_PRO_MARGIN_X + 34}' y='{note_y + 19}' "
            f"font-size='10.5' fill='#475569'>{_esc(spec.note)}</text>"
        )
    # Legend.
    out.append(
        f"<line x1='{_PRO_MARGIN_X}' y1='{legend_y}' x2='{width - _PRO_MARGIN_X}' y2='{legend_y}' stroke='#e2e8f0'/>"
    )
    lx = _PRO_MARGIN_X
    ly = legend_y + 22
    out.append(f"<text class='legend' x='{lx}' y='{ly}' font-weight='700'>Nodes</text>")
    lx += 52
    for name, (accent, _, _) in (("Data", _PRO_STAGE["data"]), ("Generate", _PRO_STAGE["gen"])):
        out.append(f"<rect x='{lx}' y='{ly - 11}' width='34' height='12' rx='3' fill='#ffffff' stroke='{accent}'/>")
        out.append(f"<rect x='{lx}' y='{ly - 11}' width='5' height='12' rx='2' fill='{accent}'/>")
        out.append(f"<text class='legend' x='{lx + 40}' y='{ly}'>{name}</text>")
        lx += 40 + len(name) * 7 + 34
    for name, (accent, _, _) in (("Assure", _PRO_STAGE["review"]), ("Publish", _PRO_STAGE["final"])):
        out.append(f"<rect x='{lx}' y='{ly - 11}' width='34' height='12' rx='3' fill='#ffffff' stroke='{accent}'/>")
        out.append(f"<rect x='{lx}' y='{ly - 11}' width='5' height='12' rx='2' fill='{accent}'/>")
        out.append(f"<text class='legend' x='{lx + 40}' y='{ly}'>{name}</text>")
        lx += 40 + len(name) * 7 + 34
    lx = _PRO_MARGIN_X
    ly += 22
    out.append(f"<text class='legend' x='{lx}' y='{ly}' font-weight='700'>Edges</text>")
    lx += 52
    edge_kinds: tuple[tuple[str, str], ...] = (
        ("flow", "sequential"),
        ("fanout", "Send fan-out"),
        ("loop", "revise loop ≤N"),
        ("alt", "conditional"),
    )
    for kind, caption in edge_kinds:
        color, w, dash = _PRO_EDGE[kind]
        dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
        out.append(
            f"<line x1='{lx}' y1='{ly - 4}' x2='{lx + 34}' y2='{ly - 4}' "
            f"stroke='{color}' stroke-width='{w}'{dash_attr}/>"
        )
        out.append(f"<text class='legend' x='{lx + 40}' y='{ly}'>{caption}</text>")
        lx += 40 + len(caption) * 7 + 30
    out.append(
        f"<text class='caption' x='{width - _PRO_MARGIN_X}' y='{height - 12}' text-anchor='end'>"
        "Send / Command routes curated from the builder · regenerate: "
        "python -m knowledge_engine.scripts.generate_graph_svg</text>"
    )
    out.append("</svg>\n")
    return "\n".join(out)


class _StubSearch(SearchPort):
    """Offline search stub: satisfies constructors, never runs."""

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Return no sources.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Always empty.
        """
        return []


class _StubScrape(ScrapePort):
    """Offline scrape stub: satisfies constructors, never runs."""

    async def to_markdown(self, url: str) -> DocumentBody:
        """Raise instead of scraping.

        Args:
            url: Page URL to render.

        Returns:
            DocumentBody: Never returned.

        Raises:
            RuntimeError: Always, this stub never runs.
        """
        raise RuntimeError("stub scrape port never runs")


class _StubProvider(ProviderPort):
    """Offline provider stub: satisfies constructors, never runs."""

    async def complete(self, req: ChatRequest) -> ChatResult:
        """Raise instead of completing.

        Args:
            req: Chat request.

        Returns:
            ChatResult: Never returned.

        Raises:
            RuntimeError: Always, this stub never runs.
        """
        raise RuntimeError("stub provider never runs")

    async def extract(self, req: ExtractionRequest) -> object:
        """Raise instead of extracting.

        Args:
            req: Extraction request.

        Returns:
            object: Never returned.

        Raises:
            RuntimeError: Always, this stub never runs.
        """
        raise RuntimeError("stub provider never runs")


def _stub_nodeset() -> graphs.NodeSet:
    """Build a NodeSet wired to offline stubs (graph topology only).

    Returns:
        graphs.NodeSet: Node set with stub adapters.
    """
    from knowledge_engine.orchestration.nodes import (
        AggregateNode,
        BodiesNode,
        BranchNode,
        CriticNode,
        DecomposeNode,
        FetchBodyNode,
        IngestNode,
        IntentNode,
        JudgeNode,
        RelevanceNode,
        RenderNode,
        ReviewNode,
        SynthesizeNode,
    )

    search = _StubSearch()
    scrape = _StubScrape()
    provider = _StubProvider()
    ingest = IngestNode(search)
    relevance = RelevanceNode(provider)
    fetch_body = FetchBodyNode(scrape)
    synthesize = SynthesizeNode(provider)
    return graphs.NodeSet(
        ingest=ingest,
        relevance=relevance,
        bodies=BodiesNode(),
        fetch_body=fetch_body,
        synthesize=synthesize,
        judge=JudgeNode(provider),
        review=ReviewNode(),
        render=RenderNode(),
        intent=IntentNode(provider),
        decompose=DecomposeNode(provider),
        branch=BranchNode(
            ingest=ingest,
            relevance=relevance,
            fetch_body=fetch_body,
            synthesize=synthesize,
        ),
        aggregate=AggregateNode(provider),
        critic=CriticNode(provider),
    )


def compiled_graph(workflow: str) -> Any:
    """Compile a workflow graph from stub-wired nodes (topology only).

    Args:
        workflow: One of ``research``, ``qa``, ``summary``, ``deep``.

    Returns:
        Any: Compiled graph.

    Raises:
        ValueError: When the workflow name is unknown.
    """
    if workflow not in WORKFLOWS:
        raise ValueError(f"unknown workflow: {workflow!r}")
    return graphs.build_workflow(workflow, _stub_nodeset())


def _layout(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """Assign grid positions by topological depth from START.

    Args:
        node_ids: Node identifiers.
        edges: Directed edges as ``(source, target)`` pairs.

    Returns:
        dict[str, tuple[int, int]]: Node identifier to ``(x, y)`` position.
    """
    # Back/cycle edges never push layout columns outward: depths only grow
    # along forward edges (see _depths).
    depth = _depths(node_ids, edges)
    columns: dict[int, list[str]] = {}
    for node_id in node_ids:
        columns.setdefault(depth[node_id], []).append(node_id)
    positions = {}
    for column, members in columns.items():
        for row, node_id in enumerate(sorted(members)):
            positions[node_id] = (20 + column * 220, 30 + row * 70)
    return positions


def _stage_for(node_id: str) -> str:
    """Map a node identifier to a diagram stage.

    Args:
        node_id: Node identifier.

    Returns:
        str: Stage key.
    """
    clean = node_id.strip("_").lower()
    if clean in ("__start__", "ingest", "start"):
        return "data"
    if clean in ("review", "judge", "critic"):
        return "review"
    if clean in ("render", "__end__", "end"):
        return "final"
    return "gen"


def render_workflow_svg(workflow: str) -> str:
    """Render one workflow's live compiled graph as SVG.

    Args:
        workflow: One of ``research``, ``qa``, ``summary``, ``deep``.

    Returns:
        str: SVG document.
    """
    graph = compiled_graph(workflow).get_graph()
    node_ids = [node.id if hasattr(node, "id") else str(node) for node in graph.nodes]
    edges = [(edge.source, edge.target) for edge in graph.edges]
    positions = _layout(node_ids, edges)
    width = 220 * (max(positions.values(), key=lambda position: position[0])[0] // 220 + 1) + 20
    height = max(position[1] for position in positions.values()) + 100
    out = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        "<style>"
        " text{font-family:Inter,Helvetica,Arial,sans-serif;fill:#111827;font-size:11px}"
        " .sub{font-size:9px;fill:#374151}"
        " .lbl{font-size:9px;fill:#6b7280;font-style:italic}"
        " .edge{stroke:#9ca3af;stroke-width:1.3;fill:none}"
        " .cycle{stroke:#dc2626;stroke-width:1.6;stroke-dasharray:5,3}"
        " .legend{font-size:10px;fill:#111827}"
        "</style>",
    ]
    depths = _depths(node_ids, edges)
    for source, target in edges:
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        backward = depths[target] <= depths[source]
        cls = "cycle" if backward else "edge"
        label = "conditional" if _is_conditional(graph, source, target) else ""
        start_x = x1 + BW if x1 < x2 or backward else x1
        end_x = x2 if (x1 < x2 or backward) else x2 + BW
        mid_x = (start_x + end_x) / 2
        out.append(
            f"<path class='{cls}' d='M{start_x},{y1 + BH / 2} "
            f"C{mid_x},{y1 + BH / 2} {mid_x},{y2 + BH / 2} {end_x},{y2 + BH / 2}'/>"
        )
        if label:
            out.append(f"<text class='lbl' x='{mid_x + 6}' y='{(y1 + y2) / 2}'>{label}</text>")
    for node_id in node_ids:
        x, y = positions[node_id]
        fill, stroke = STAGE.get(_stage_for(node_id), STAGE["data"])
        out.append(
            f"<rect x='{x}' y='{y}' width='{BW}' height='{BH}' rx='6' "
            f"fill='{fill}' stroke='{stroke}' stroke-width='1.4'/>"
        )
        out.append(f"<text x='{x + 12}' y='{y + 14}' style='font-weight:600'>{_esc(node_id)}</text>")
        out.append(f"<text class='sub' x='{x + 12}' y='{y + 25}'>{_esc(_stage_for(node_id))}</text>")
    out.append(
        f"<text class='legend' x='20' y='{height - 12}'>Knowledge Engine {workflow} workflow (live topology)</text>"
    )
    out.append("</svg>\n")
    return "\n".join(out)


def _depths(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Compute topological depths shared by layout and edge styling.

    Args:
        node_ids: Node identifiers.
        edges: Directed edges as ``(source, target)`` pairs.

    Returns:
        dict[str, int]: Node identifier to depth.
    """
    depth: dict[str, int] = {node_id: 0 for node_id in node_ids}
    changed = True
    while changed:
        changed = False
        for source, target in edges:
            if source in depth and target in depth and depth[target] < depth[source] + 1:
                if depth[target] <= depth[source]:
                    continue
                depth[target] = depth[source] + 1
                changed = True
    return depth


def _is_conditional(graph: Any, source: str, target: str) -> bool:
    """Report whether a graph edge is conditional.

    Args:
        graph: Compiled-graph topology object.
        source: Edge source node identifier.
        target: Edge target node identifier.

    Returns:
        bool: True for conditional edges.
    """
    for edge in graph.edges:
        if edge.source == source and edge.target == target:
            return bool(getattr(edge, "conditional", False))
    return False


def main() -> int:
    """Write professional per-workflow SVGs to ``docs/diagrams/``.

    ``graph.svg`` stays the research alias for existing links.

    Returns:
        int: Exit code.
    """
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    for workflow in WORKFLOWS:
        path = DIAGRAM_DIR / f"graph-{workflow}.svg"
        path.write_text(render_professional_svg(workflow), encoding="utf-8")
        print(f"wrote {path}")
    alias = DIAGRAM_DIR / "graph.svg"
    alias.write_text(render_professional_svg("research"), encoding="utf-8")
    print(f"wrote {alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

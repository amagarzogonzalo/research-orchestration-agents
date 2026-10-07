"""Generate a polished SVG diagram of the Knowledge Engine LangGraph pipeline.

Reads (a hand-curated mirror of) the topology from
:mod:`knowledge_engine.orchestration.graphs` and renders ``graph.svg`` at the
repo root — nodes grouped by stage, color-coded, with conditional-routing
labels and the evaluator-optimizer loop highlighted. Mirrors the digest-agent
``gen_graph_svg`` tool.

Run:  python -m knowledge_engine.scripts.generate_graph_svg
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from knowledge_engine.core.ports import ProviderPort, ScrapePort, SearchPort
from knowledge_engine.orchestration import graphs
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import ChatRequest, ChatResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord

REPO_ROOT = Path(__file__).resolve().parents[3]

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
    out.append("</svg>")
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
    out.append("</svg>")
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
    """Write per-workflow SVGs to the repo root and return exit code 0.

    ``graph.svg`` stays the research alias for existing links.

    Returns:
        int: Exit code.
    """
    for workflow in WORKFLOWS:
        path = REPO_ROOT / f"graph-{workflow}.svg"
        path.write_text(render_workflow_svg(workflow), encoding="utf-8")
        print(f"wrote {path}")
    alias = REPO_ROOT / "graph.svg"
    alias.write_text(render_svg(), encoding="utf-8")
    print(f"wrote {alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

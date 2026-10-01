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

REPO_ROOT = Path(__file__).resolve().parents[3]

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


def main() -> int:
    """Write ``graph.svg`` to the repo root and return exit code 0.

    Returns:
        int: Exit code.
    """
    path = REPO_ROOT / "graph.svg"
    path.write_text(render_svg(), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

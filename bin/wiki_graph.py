#!/usr/bin/env python3
"""Dump the wiki knowledge graph (nodes/edges/backlinks/communities) as JSON.

uv run python bin/wiki_graph.py [ROOT] [out.json]   # default: research_store/wiki
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from knowledge_engine.infrastructure.wiki import WikiGraph


def main() -> int:
    """Write the graph JSON.

    Returns:
        int: 0 on success.
    """
    args = sys.argv[1:]
    root = args[0] if args and not args[0].endswith(".json") else "research_store/wiki"
    out = next((a for a in args if a.endswith(".json")), "wiki-graph.json")
    graph = WikiGraph.from_vault(root)
    payload = graph.to_json()
    Path(out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"wiki graph: {len(payload['nodes'])} nodes, {len(payload['edges'])} edges, "
        f"{len(payload['communities'])} pages, {len(set(payload['communities'].values()))} communities -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

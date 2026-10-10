"""WikiGraph — deterministic knowledge-graph of a wiki vault (Loop 5 remainder).

Pure-Python, no dependencies, no LLM:

* **nodes** — every live page (slug, type, title, date);
* **edges** — every ``[[wikilink]]`` pair (directed, src -> dst);
* **backlinks** — inbound links per page;
* **shared-thread edges** — pages filed from the same thread run (the
  "source overlap" signal of the 4-signal model);
* **communities** — label-propagation clustering over the link graph
  (a lightweight stand-in for Louvain: deterministic, seeded ordering).

The graph is derivable from markdown in one pass — nothing is stored, nothing
is guessed; it is the ``.wiki.db`` ``links``/``notes`` tables as a typed view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_engine.infrastructure.wiki import common

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


@dataclass(frozen=True)
class WikiNode:
    """One wiki page (vertex).

    Attributes:
        slug: Page slug.
        type: Page type (``source``/``concept``/``entity``/...).
        title: Page title (fallback to filename).
        date: Frontmatter date, when present.
        thread: Filing thread id, when present (source-overlap signal).
    """

    slug: str
    type: str = ""
    title: str = ""
    date: str = ""
    thread: str = ""


@dataclass(frozen=True)
class WikiEdge:
    """One directed link between pages.

    Attributes:
        src: Source slug.
        dst: Target slug.
        kind: ``link`` (wikilink) or ``shared-thread``.
    """

    src: str
    dst: str
    kind: str = "link"


@dataclass
class WikiGraph:
    """Node/edge view of the vault with backlinks + communities.

    Attributes:
        nodes: Slug -> page metadata.
        edges: All directed edges.
        communities: Label-propagation community id per slug.
    """

    nodes: dict[str, WikiNode] = field(default_factory=dict)
    edges: list[WikiEdge] = field(default_factory=list)
    communities: dict[str, str] = field(default_factory=dict)

    def backlinks(self, slug: str) -> list[str]:
        """Inbound links to a page.

        Args:
            slug: Target page slug.

        Returns:
            list[str]: Slugs that link to it, sorted.
        """
        return sorted(edge.src for edge in self.edges if edge.dst == slug and edge.kind == "link")

    def neighbors(self, slug: str, *, backlinks: bool = True) -> set[str]:
        """Adjacent slugs (wikilinks + shared-thread edges).

        The 4-signal "source overlap" lives here too: pages filed from the
        same thread are adjacent even without an explicit link.

        Args:
            slug: Page slug.
            backlinks: Include inbound sources when True.

        Returns:
            set[str]: Adjacent slugs.
        """
        out = {edge.dst for edge in self.edges if edge.src == slug}
        if backlinks:
            out |= {edge.src for edge in self.edges if edge.dst == slug}
        return out

    def hub_slugs(self, limit: int = 5) -> list[str]:
        """Most-linked pages (bridge/hub signal).

        Args:
            limit: Max results.

        Returns:
            list[str]: Slugs ranked by (inbound + outbound) degree.
        """
        degree: dict[str, int] = {}
        for edge in self.edges:
            if edge.kind != "link":
                continue
            degree[edge.src] = degree.get(edge.src, 0) + 1
            degree[edge.dst] = degree.get(edge.dst, 0) + 1
        ranked = sorted(degree, key=lambda s: (-degree[s], s))
        return ranked[:limit]

    def to_json(self) -> dict[str, Any]:
        """Serializable graph for scripting/visualization.

        Returns:
            dict[str, Any]: Nodes, edges (src/dst/kind), communities.
        """
        return {
            "nodes": [
                {
                    "slug": n.slug,
                    "type": n.type,
                    "title": n.title,
                    "date": n.date,
                    "community": self.communities.get(n.slug, ""),
                }
                for n in self.nodes.values()
            ],
            "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind} for e in self.edges],
            "backlinks": {slug: self.backlinks(slug) for slug in self.nodes},
            "communities": dict(self.communities),
        }

    @classmethod
    def from_vault(cls, root: str | Path) -> WikiGraph:
        """Build the graph from a vault directory.

        Two passes: collect every page (so links anywhere resolve to any page,
        including pages scanned later), then emit edges.

        Args:
            root: Wiki root (same layout as :class:`FileWikiStore`).

        Returns:
            WikiGraph: Nodes + edges + communities.
        """
        graph = cls()
        root_path = Path(root)
        bodies: dict[str, str] = {}
        threads: dict[str, list[str]] = {}
        if root_path.is_dir():
            for name in common.PAGE_DIRS:
                d = root_path / name
                if not d.is_dir():
                    continue
                for p in sorted(d.glob("*.md")):
                    fm, body = common.split_frontmatter(p.read_text(encoding="utf-8"))
                    slug = p.stem
                    graph.nodes[slug] = WikiNode(
                        slug=slug,
                        type=str(fm.get("type") or ""),
                        title=str(fm.get("title") or slug),
                        date=str(fm.get("date") or ""),
                        thread=str(fm.get("thread") or ""),
                    )
                    bodies[slug] = body
                    if graph.nodes[slug].thread:
                        threads.setdefault(graph.nodes[slug].thread, []).append(slug)

        for slug, body in bodies.items():
            outbound = {t for t in _WIKILINK.findall(body) if t in graph.nodes}
            for target in sorted(outbound):
                if target != slug:
                    graph.edges.append(WikiEdge(src=slug, dst=target, kind="link"))

        seen_pairs: set[tuple[str, str, str]] = set()
        for _thread, slugs in threads.items():
            pairs = sorted((a, b) for a in slugs for b in slugs if a != b)
            for a, b in pairs:
                key = (a, b, "shared-thread")
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    graph.edges.append(WikiEdge(src=a, dst=b, kind="shared-thread"))

        graph.communities = _label_propagation(graph)
        return graph


def _label_propagation(graph: WikiGraph, iters: int = 4) -> dict[str, str]:
    """Deterministic label-propagation communities over the link graph.

    Args:
        graph: Built graph.
        iters: Propagation rounds (small d prove convergence with seed order).

    Returns:
        dict[str, str]: Community id per slug (ids are the lexicographically
        smallest member slug of each cluster).
    """
    labels = {slug: slug for slug in sorted(graph.nodes)}
    for _ in range(iters):
        changed = False
        for slug in sorted(labels):
            neighbors = graph.neighbors(slug)
            if not neighbors:
                continue
            votes: dict[str, int] = {}
            for nb in sorted(neighbors):
                votes[labels.get(nb, nb)] = votes.get(labels.get(nb, nb), 0) + 1
            best = max(votes, key=lambda label: (votes[label], label))
            if best != labels[slug]:
                labels[slug] = best
                changed = True
        if not changed:
            break
    # Canonicalize: each community takes its lexicographically smallest slug.
    communities = {slug: labels[slug] for slug in labels}
    canon: dict[str, str] = {}
    for slug, label in sorted(communities.items()):
        canon.setdefault(label, slug)
    return {slug: canon[label] for slug, label in communities.items()}


__all__ = ["WikiEdge", "WikiGraph", "WikiNode"]

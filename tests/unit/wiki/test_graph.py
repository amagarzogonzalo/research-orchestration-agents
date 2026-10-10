"""Unit tests: WikiGraph deterministic nodes/edges/backlinks/communities."""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki import WikiGraph


def _seed_vault(root: Path) -> None:
    """Four-page wiki: A links B; B links C; D isolated; C/D share a thread.

    Args:
        root: Wiki root.
    """
    (root / "concepts").mkdir(parents=True)
    (root / "entities").mkdir(parents=True)
    (root / "sources").mkdir(parents=True)
    (root / "concepts" / "a.md").write_text(
        "---\ntype: concept\ntitle: A\ndate: 2026-10-09\n---\n\n sees [[b]] and references [[c]] " * 3,
        encoding="utf-8",
    )
    (root / "concepts" / "b.md").write_text(
        "---\ntype: concept\ntitle: B\ndate: 2026-10-09\n---\n\n back to [[c]] " * 3,
        encoding="utf-8",
    )
    (root / "entities" / "c.md").write_text(
        "---\ntype: entity\ntitle: C\ndate: 2026-10-09\nthread: t1\n---\n\nthe entity under study " * 3,
        encoding="utf-8",
    )
    (root / "sources" / "d.md").write_text(
        "---\ntype: source\ntitle: D\ndate: 2026-10-09\nthread: t1\n---\n\nan isolated source file " * 3,
        encoding="utf-8",
    )


def test_nodes_backlinks_and_shared_thread() -> None:
    """Links, backlinks, and shared-thread edges are computed exactly."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "wiki"
        _seed_vault(root)
        graph = WikiGraph.from_vault(root)
    assert set(graph.nodes) == {"a", "b", "c", "d"}
    assert graph.nodes["c"].type == "entity"
    assert ("a", "b", "link") in [(e.src, e.dst, e.kind) for e in graph.edges]
    assert ("a", "c", "link") in [(e.src, e.dst, e.kind) for e in graph.edges]
    assert ("c", "d", "shared-thread") in [(e.src, e.dst, e.kind) for e in graph.edges]
    assert graph.backlinks("c") == ["a", "b"]
    assert "d" in graph.neighbors("c")


def test_orphan_detection() -> None:
    """A page with no inbound links and no outgoing link is an orphan."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "wiki"
        (root / "sources").mkdir(parents=True)
        (root / "sources" / "lonely.md").write_text(
            "---\ntype: source\ntitle: Lonely\ndate: 2026-10-09\n---\n\nnothing links to this " * 3,
            encoding="utf-8",
        )
        graph = WikiGraph.from_vault(root)
    assert graph.backlinks("lonely") == []
    assert graph.hub_slugs() == []


def test_communities_are_deterministic_and_connected() -> None:
    """Label propagation returns stable ids; linked pages share a community."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "wiki"
        _seed_vault(root)
        g1 = WikiGraph.from_vault(root)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "wiki"
        _seed_vault(root)
        g2 = WikiGraph.from_vault(root)
    assert g1.communities == g2.communities, "communities must be reproducible"
    # a--b--c chain: a and c should end in the same community (via b).
    assert g1.communities["a"] == g1.communities["c"]
    # d shares a thread with c -> same community via shared-thread edge.
    assert g1.communities["c"] == g1.communities["d"]


def test_to_json_serializable() -> None:
    """to_json round-trips as plain JSON-safe dicts."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "wiki"
        _seed_vault(root)
        payload = WikiGraph.from_vault(root).to_json()
    assert len(payload["nodes"]) == 4
    json.dumps(payload)
    assert payload["backlinks"]["c"] == ["a", "b"]

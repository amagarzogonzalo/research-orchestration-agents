"""Unit tests: FileVaultLinter deterministic findings on seeded fixtures."""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki.lint import FileVaultLinter


def _seed_vault(root: Path, pages: dict[str, str], index: str | None = None) -> None:
    """Create a fixture vault with the given pages.

    Args:
        root: Vault root.
        pages: slug -> page text.
        index: Optional index.md content.
    """
    (root / "sources").mkdir(parents=True, exist_ok=True)
    for slug, text in pages.items():
        (root / "sources" / f"{slug}.md").write_text(text, encoding="utf-8")
    if index is not None:
        (root / "index.md").write_text(index, encoding="utf-8")


async def test_clean_vault_is_ok(tmp_path: Path) -> None:
    """A well-formed, indexed vault reports no findings."""
    _seed_vault(
        tmp_path,
        {
            "rag": "---\ntype: source\ntitle: RAG\ndate: 2026-10-09\n---\n\n"
            + "Retrieval augmented generation reduces hallucinations " * 4,
        },
        index="# Knowledge Base Index\n\n## Sources\n- [RAG](sources/rag.md) — summary\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert report.ok
    assert report.pages == 1


async def test_missing_frontmatter_detected(tmp_path: Path) -> None:
    """A page without required keys lands in missing_frontmatter."""
    _seed_vault(tmp_path, {"bad": "no frontmatter here at all"})
    report = await FileVaultLinter(tmp_path).lint()
    assert report.missing_frontmatter == ["bad"]


async def test_broken_wikilink_detected(tmp_path: Path) -> None:
    """A [[link]] to a page that does not exist is reported."""
    _seed_vault(
        tmp_path,
        {
            "a": "---\ntype: source\ntitle: A\ndate: 2026-10-09\n---\n\nsee [[missing-page]] " + "x " * 100,
        },
        index="# Index\n- [A](a.md)\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert report.broken_links == ["a -> missing-page"]


async def test_orphan_and_index_drift_detected(tmp_path: Path) -> None:
    """An unindexed page with no inbound links is orphan + drift."""
    _seed_vault(
        tmp_path,
        {
            "lonely": "---\ntype: source\ntitle: Lonely\ndate: 2026-10-09\n---\n\n" + "body " * 100,
        },
        index="# Index\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert "lonely" in report.orphans
    assert report.index_drift == ["lonely"]


async def test_thin_page_detected(tmp_path: Path) -> None:
    """A page under 200 body chars is thin."""
    _seed_vault(
        tmp_path,
        {"thin": "---\ntype: source\ntitle: Thin\ndate: 2026-10-09\n---\n\nshort body"},
        index="# Index\n- [Thin](thin.md)\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert report.thin_pages == ["thin"]


async def test_indexed_page_is_not_orphan(tmp_path: Path) -> None:
    """A page referenced from index.md is not a false orphan (Loop 0 rule)."""
    _seed_vault(
        tmp_path,
        {"ok": "---\ntype: source\ntitle: OK\ndate: 2026-10-09\n---\n\n" + "solid body text " * 30},
        index="# Index\n- [OK](sources/ok.md) — fine\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert report.orphans == []
    assert report.index_drift == []


async def test_inbound_wikilink_clears_orphan(tmp_path: Path) -> None:
    """A page linked from another page is not an orphan even if unindexed."""
    _seed_vault(
        tmp_path,
        {
            "hub": "---\ntype: source\ntitle: Hub\ndate: 2026-10-09\n---\n\nlinks to [[spoke]] " + "y " * 100,
            "spoke": "---\ntype: source\ntitle: Spoke\ndate: 2026-10-09\n---\n\n" + "body " * 100,
        },
        index="# Index\n- [Hub](hub.md)\n",
    )
    report = await FileVaultLinter(tmp_path).lint()
    assert "spoke" not in report.orphans

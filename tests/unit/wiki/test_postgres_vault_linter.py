"""Unit tests: PostgresVaultLinter (deterministic, one query, no N downloads)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fakes import fake_pg_connection  # noqa: E402

from knowledge_engine.infrastructure.wiki import PostgresVaultLinter, PostgresWikiStore  # noqa: E402


def _article(tmp_path: Path, text: str) -> str:
    """Write a fixture article and return its path.

    Args:
        tmp_path: Per-test temp dir.
        text: Article markdown.

    Returns:
        str: Article path.
    """
    path = tmp_path / "article.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


async def test_lint_reports_broken_link_and_orphans(tmp_path: Path) -> None:
    """A page linking to a missing slug reports a broken link + orphan."""
    conn = fake_pg_connection()
    store = PostgresWikiStore(lambda: conn)
    await store.ingest("t1", "alpha topic", _article(tmp_path, "alpha body [[missing-page]] here"))
    await store.ingest("t2", "omega topic", _article(tmp_path, "omega standalone body"))

    report = await PostgresVaultLinter(store).lint()

    assert report.pages == 2
    assert any("missing-page" in entry for entry in report.broken_links)
    assert "omega-topic" in report.orphans


async def test_lint_clean_when_linked(tmp_path: Path) -> None:
    """Reciprocally linked pages are neither broken nor orphans."""
    conn = fake_pg_connection()
    store = PostgresWikiStore(lambda: conn)
    await store.ingest("t1", "alpha topic", _article(tmp_path, "alpha body [[beta-topic]] long content"))
    await store.ingest("t2", "beta topic", _article(tmp_path, "beta body [[alpha-topic]] long content"))

    report = await PostgresVaultLinter(store).lint()

    assert report.broken_links == []
    assert "alpha-topic" not in report.orphans
    assert "beta-topic" not in report.orphans

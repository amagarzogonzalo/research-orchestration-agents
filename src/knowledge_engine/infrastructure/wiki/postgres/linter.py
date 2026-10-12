"""Deterministic vault lint over the wiki v2 schema via SQL views (SRP)."""

from __future__ import annotations

from typing import Any

from knowledge_engine.core.ports import VaultLintPort
from knowledge_engine.schemas.wiki import LintReport

_BROKEN = "SELECT src, dst_slug FROM wiki.v_broken_links"
_ORPHANS = "SELECT kind, slug FROM wiki.v_orphans"
_PAGES = "SELECT count(*) FROM wiki.v_pages WHERE status = 'active'"


class PostgresVaultLinter(VaultLintPort):
    """Lint active pages from the relational views (orphans/broken links).

    ``index_drift`` stays empty: the schema is the index. Only broken links,
    orphans, and (thin/frontmatter checks can be added from ``v_pages`` when
    needed) are reported here via server-side SQL.
    """

    def __init__(self, store: Any) -> None:
        """Create a PostgresVaultLinter over a store's connection factory.

        Args:
            store: A PostgresWikiStore (shared connection factory).
        """
        self._store = store

    async def lint(self) -> LintReport:
        """Scan active pages and report deterministic structural findings.

        Returns:
            LintReport: Findings; ``ok`` is True when the vault is clean.
        """
        async with self._store._txn() as cur:
            await cur.execute(_BROKEN)
            broken = [f"{src} -> {dst}" for src, dst in await cur.fetchall()]
            await cur.execute(_ORPHANS)
            orphans = [slug for _kind, slug in await cur.fetchall()]
            await cur.execute(_PAGES)
            row = await cur.fetchone()
            pages = int(row[0]) if row else 0
        return LintReport(
            root="postgres://wiki",
            pages=pages,
            missing_frontmatter=[],
            broken_links=sorted(broken),
            orphans=sorted(orphans),
            thin_pages=[],
            index_drift=[],
        )


__all__ = ["PostgresVaultLinter"]

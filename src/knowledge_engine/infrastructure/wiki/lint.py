"""FileVaultLinter — deterministic wiki health check on the local vault.

Delegates to the shared :mod:`lint_core` scan; this adapter only enumerates
the vault's markdown files.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.core.ports import VaultLintPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.lint_core import lint_pages
from knowledge_engine.schemas.wiki import LintReport


class FileVaultLinter(VaultLintPort):
    """Scan a local wiki vault and report structural issues.

    Args:
        root: Wiki root directory (same layout as :class:`FileWikiStore`).
    """

    def __init__(self, root: str | Path = "research_store/wiki") -> None:
        self.root = Path(root)

    async def lint(self) -> LintReport:
        """Scan the vault and report findings.

        Returns:
            LintReport: All findings; ``ok`` is True when the vault is clean.
        """
        pages: dict[str, str] = {}
        if self.root.is_dir():
            for name in common.PAGE_DIRS:
                d = self.root / name
                if not d.is_dir():
                    continue
                for p in sorted(d.glob("*.md")):
                    pages[p.stem] = p.read_text(encoding="utf-8")
        index_path = self.root / common.INDEX_NAME
        index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else None
        return lint_pages(pages, root=str(self.root), index_text=index_text)


__all__ = ["FileVaultLinter"]

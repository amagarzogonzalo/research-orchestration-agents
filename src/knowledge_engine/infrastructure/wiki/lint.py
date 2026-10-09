"""FileVaultLinter — deterministic wiki health check (ADR wiki-loop0).

No LLM: broken ``[[wikilinks]]``, missing frontmatter, thin pages, orphans,
and index drift are all computable from the files. Orphans count a page with
neither inbound ``[[wikilinks]]`` nor an ``index.md`` entry — Loop 0's
deterministic ingest files index entries for every page, so a filed page is
never a false orphan; strict graph orphans stay an L1+ metric.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from knowledge_engine.core.ports import VaultLintPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.schemas.wiki import LintReport

_THIN_CHARS = 200
_REQUIRED_FM_KEYS = ("type", "title", "date")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]]")


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
        pages: dict[str, tuple[Path, dict[str, Any], str, str]] = {}
        if self.root.is_dir():
            for name in common.PAGE_DIRS:
                d = self.root / name
                if not d.is_dir():
                    continue
                for p in sorted(d.glob("*.md")):
                    text = p.read_text(encoding="utf-8")
                    fm, body = common.split_frontmatter(text)
                    pages.setdefault(p.stem, (p, fm, body, text))

        inbound: dict[str, int] = {slug: 0 for slug in pages}
        indexed: set[str] = set()
        index_path = self.root / common.INDEX_NAME
        if index_path.is_file():
            index_text = index_path.read_text(encoding="utf-8")
            indexed = {
                slug
                for slug in pages
                if f"]({'sources/' + slug + '.md'})" in index_text or f"({slug}.md)" in index_text
            }

        missing_fm: list[str] = []
        broken: list[str] = []
        thin: list[str] = []
        for slug, (_, fm, body, text) in pages.items():
            if any(key not in fm for key in _REQUIRED_FM_KEYS):
                missing_fm.append(slug)
            if len(body.strip()) < _THIN_CHARS:
                thin.append(slug)
            for target in _WIKILINK.findall(text):
                stem = target.split("|")[0].split("#")[0].strip()
                if stem and stem in pages and stem != slug:
                    inbound[stem] += 1
                elif stem and stem not in pages:
                    broken.append(f"{slug} -> {stem}")

        orphans = [slug for slug, count in inbound.items() if count == 0 and slug not in indexed]
        drift = sorted(set(pages) - indexed)

        return LintReport(
            root=str(self.root),
            pages=len(pages),
            missing_frontmatter=sorted(missing_fm),
            broken_links=sorted(set(broken)),
            orphans=sorted(orphans),
            thin_pages=sorted(thin),
            index_drift=drift,
        )


__all__ = ["FileVaultLinter"]

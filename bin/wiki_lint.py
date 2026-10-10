#!/usr/bin/env python3
"""Wiki lint gate: deterministic health check over the store's wiki.

Loop 5 (per docs/LLM_WIKI.md): the self-maintaining wiki is enforced like
code — broken links, orphans, missing frontmatter, thin pages and index
drift fail the gate instead of accumulating silently.

    uv run python bin/wiki_lint.py [ROOT]   # default: research_store/wiki
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")

from knowledge_engine.infrastructure.wiki import FileVaultLinter


def main() -> int:
    """Lint the wiki root and exit non-zero on findings.

    Returns:
        int: 0 when clean, 1 when the wiki needs curation.
    """
    root = sys.argv[1] if len(sys.argv) > 1 else "research_store/wiki"
    if not Path(root).is_dir():
        print(f"wiki root not found: {root} (nothing to lint)")
        return 0
    report = asyncio.run(FileVaultLinter(root).lint())
    print(
        f"wiki: {report.pages} pages, missing_frontmatter={len(report.missing_frontmatter)}, "
        f"broken_links={len(report.broken_links)}, orphans={len(report.orphans)}, "
        f"thin={len(report.thin_pages)}, index_drift={len(report.index_drift)}"
    )
    for slug in report.missing_frontmatter:
        print(f"  missing frontmatter: {slug}")
    for entry in report.broken_links:
        print(f"  broken link: {entry}")
    for slug in report.orphans:
        print(f"  orphan: {slug}")
    for slug in report.thin_pages:
        print(f"  thin page: {slug}")
    for slug in report.index_drift:
        print(f"  index drift: {slug}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

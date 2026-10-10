#!/usr/bin/env python3
"""Wiki deep-lint CLI — deterministic + optional LLM cross-page pass.

        uv run python bin/wiki_lint_deep.py [ROOT]          # deterministic only
        OPENROUTER_API_KEY=... uv run python bin/wiki_lint_deep.py [ROOT]  # + LLM pass

The LLM pass degrades gracefully: no provider key, no provider-configured
engine, or a provider failure yields the deterministic report only.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from knowledge_engine.infrastructure.wiki import WikiDeepLinter


def _provider() -> object | None:
    """Build a provider from the environment, or None when not configured.

    Returns:
        object | None: A configured ProviderPort or None (deterministic only).
    """
    try:
        from knowledge_engine.engine import _default_provider

        return _default_provider()
    except Exception:  # noqa: BLE001 - no keys/transport -> deterministic only
        return None


def main() -> int:
    """Run the deep lint.

    Returns:
        int: 0 when clean, 1 when findings exist.
    """
    root = sys.argv[1] if len(sys.argv) > 1 else "research_store/wiki"
    linter = WikiDeepLinter(root, provider=_provider())
    structural, deep = asyncio.run(linter.lint_deep())
    print(
        f"structural: {structural.pages} pages, missing_fm={len(structural.missing_frontmatter)}, "
        f"broken={len(structural.broken_links)}, orphans={len(structural.orphans)}, "
        f"thin={len(structural.thin_pages)}, drift={len(structural.index_drift)}"
    )
    for entry in structural.broken_links:
        print(f"  broken link: {entry}")
    for entry in structural.orphans:
        print(f"  orphan: {entry}")
    if deep.contradictions or deep.stale or deep.gaps:
        print("deep findings (LLM pass):")
        for contradiction in deep.contradictions:
            print(f"  contradiction: {contradiction}")
        for stale in deep.stale:
            print(f"  stale: {stale}")
        for gap in deep.gaps:
            print(f"  gap: {gap}")
    return 0 if (structural.ok and deep.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

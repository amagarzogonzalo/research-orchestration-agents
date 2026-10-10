"""Shared deterministic lint core (used by the file and cloud vault linters).

No LLM: broken ``[[wikilinks]]``, missing frontmatter, thin pages, orphans,
and index drift are all computable from page text. Orphans count a page with
neither inbound ``[[wikilinks]]`` nor an index entry — the deterministic
ingest files index entries for every page, so a filed page is never a false
orphan; strict graph orphans stay an L1+ metric.
"""

from __future__ import annotations

import re
from typing import Any

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.schemas.wiki import LintReport

_THIN_CHARS = 200
_REQUIRED_FM_KEYS = ("type", "title", "date")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]]")


def lint_pages(pages: dict[str, str], *, root: str, index_text: str | None) -> LintReport:
    """Lint a mapping of slug/key -> page text.

    Args:
        pages: Page identifier (slug or bucket key) -> raw markdown.
        root: Vault root or bucket name (report metadata).
        index_text: ``index.md`` content (None = no index).

    Returns:
        LintReport: Findings; ``ok`` is True when the vault is clean.
    """
    fm_map: dict[str, dict[str, Any]] = {}
    body_map: dict[str, str] = {}
    for key, text in pages.items():
        slug = key.rsplit("/", 1)[-1]
        slug = slug[: -len(".md")] if slug.endswith(".md") else slug
        fm, body = common.split_frontmatter(text)
        fm_map[slug] = fm
        body_map[slug] = body

    inbound: dict[str, int] = {slug: 0 for slug in fm_map}
    indexed: set[str] = set()
    if index_text:
        indexed = {slug for slug in fm_map if f"({slug}.md)" in index_text or f"/{slug}.md)" in index_text}

    missing_fm: list[str] = []
    broken: list[str] = []
    thin: list[str] = []
    for slug, text in pages.items():
        key_slug = slug.rsplit("/", 1)[-1]
        key_slug = key_slug[: -len(".md")] if key_slug.endswith(".md") else key_slug
        fm = fm_map[key_slug]
        if any(k not in fm for k in _REQUIRED_FM_KEYS):
            missing_fm.append(key_slug)
        if len(body_map[key_slug].strip()) < _THIN_CHARS:
            thin.append(key_slug)
        for target in _WIKILINK.findall(text):
            stem = target.split("|")[0].split("#")[0].strip()
            if stem and stem in fm_map and stem != key_slug:
                inbound[stem] += 1
            elif stem and stem not in fm_map:
                broken.append(f"{key_slug} -> {stem}")

    orphans = [s for s, count in inbound.items() if count == 0 and s not in indexed]
    drift = sorted(set(fm_map) - indexed)

    return LintReport(
        root=root,
        pages=len(fm_map),
        missing_frontmatter=sorted(missing_fm),
        broken_links=sorted(set(broken)),
        orphans=sorted(orphans),
        thin_pages=sorted(thin),
        index_drift=drift,
    )


__all__ = ["lint_pages"]

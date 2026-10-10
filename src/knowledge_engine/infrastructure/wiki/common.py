"""Shared deterministic helpers for wiki adapters (ADR wiki-loop0).

Scoring, frontmatter splitting, slugging, and naming constants used by the
file and Supabase wiki adapters. No LLM, no embeddings, no network: every
helper is pure text/disk logic so replay stays deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

INDEX_NAME = "index.md"
LOG_NAME = "log.md"
OVERVIEW_NAME = "overview.md"
CACHE_NAME = ".cache.json"
FEEDBACK_NAME = ".feedback.json"
BLOCKED_NAME = ".blocked.json"

#: Deterministic ingest writes pages under these subdirectories; index/log/
#: overview live at the wiki root and are exempt from lint's orphan scan.
#: ``drafts/`` is deliberately excluded: drafts are a review area, never
#: live pages (not searched, not linted, not indexed).
PAGE_DIRS = ("sources", "synthesis", "concepts", "entities", "queries")

WORD_RE = re.compile(r"[a-z0-9]+")
_FM_BLOCK = re.compile(r"(?ms)\A---\s*\n(.*?)\n---\s*\n?")


def terms(text: str) -> list[str]:
    """Lowercase word tokens for scoring.

    Args:
        text: Query or page text.

    Returns:
        list[str]: Tokens (deduped, order-preserving).
    """
    return list(dict.fromkeys(WORD_RE.findall(text.lower())))


def score(query_terms: list[str], title: str, body: str) -> float:
    """Score one page against query terms: title matches weigh 3x.

    Args:
        query_terms: Query tokens.
        title: Page title.
        body: Page body text.

    Returns:
        float: Relevance score (0 when no term matches).
    """
    if not query_terms:
        return 0.0
    title_hits = sum(1 for t in query_terms if t in title.lower())
    body_head = body[:2000].lower()
    body_hits = sum(1 for t in query_terms if t in body_head)
    return float(title_hits * 3 + body_hits)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body).

    Args:
        text: Raw page text with an optional ``---`` frontmatter block.

    Returns:
        tuple[dict[str, Any], str]: Parsed frontmatter (empty dict when
        absent/malformed) and the body text.
    """
    match = _FM_BLOCK.match(text)
    if match is None:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text
    fm = dict(loaded) if isinstance(loaded, dict) else {}
    return fm, text[match.end() :]


def fm_title(fm: dict[str, Any], path: Path) -> str:
    """Page title from frontmatter, falling back to the filename stem.

    Args:
        fm: Parsed frontmatter mapping.
        path: Page path.

    Returns:
        str: Title text.
    """
    title = fm.get("title")
    return str(title) if title else path.stem


def slugify(text: str) -> str:
    """Kebab-case slug (ASCII-safe, max 60 chars).

    Args:
        text: Query or title text.

    Returns:
        str: Slug (``research-notes`` when nothing survives).
    """
    words = WORD_RE.findall(text.lower())[:8]
    slug = "-".join(words)[:60].strip("-")
    return slug or "research-notes"


def tldr(text: str) -> str:
    """First ~200 chars of the body as the one-line summary.

    Args:
        text: Page or article text (frontmatter included).

    Returns:
        str: Summary line.
    """
    _, body = split_frontmatter(text)
    collapsed = " ".join(body.split())
    return collapsed[:200]


def page_title(query: str) -> str:
    """Title for a filed page, derived from the query.

    Args:
        query: Research query string.

    Returns:
        str: Title (max 80 chars).
    """
    return query.strip().strip("?")[:80] or "Research Notes"


def today() -> str:
    """ISO date used by log entries and frontmatter.

    Returns:
        str: ``YYYY-MM-DD`` in UTC.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d")


def log_line(op: str, title: str) -> str:
    """One grep-parseable log entry (Karpathy prefix convention).

    Args:
        op: Operation name (ingest/query/lint).
        title: Page title.

    Returns:
        str: ``## [YYYY-MM-DD] <op> | <title>`` line.
    """
    return f"## [{today()}] {op} | {title}"


def sha256_text(text: str) -> str:
    """SHA256 hex digest of UTF-8 text (the ingest cache key).

    Args:
        text: Page or article text.

    Returns:
        str: Hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object, defaulting to empty when absent/malformed.

    Args:
        path: JSON file path.

    Returns:
        dict[str, Any]: Parsed mapping.
    """
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON object atomically enough for a cache file.

    Args:
        path: JSON file path.
        data: Mapping to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "BLOCKED_NAME",
    "CACHE_NAME",
    "FEEDBACK_NAME",
    "INDEX_NAME",
    "LOG_NAME",
    "OVERVIEW_NAME",
    "PAGE_DIRS",
    "fm_title",
    "load_json",
    "log_line",
    "page_title",
    "score",
    "sha256_text",
    "slugify",
    "split_frontmatter",
    "terms",
    "tldr",
    "today",
    "write_json",
]

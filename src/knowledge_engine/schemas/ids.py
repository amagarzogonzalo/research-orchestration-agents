"""Safe identifier contracts shared by files, checkpoints, and the HTTP API.

Thread ids become directory names under ``research_store/`` and checkpoint
roots, so accepting arbitrary text from a caller is a path-traversal risk. The
same value is used by the LangGraph checkpointer, the research store, and the
service job table; validating it once here keeps every layer consistent.
"""

from __future__ import annotations

import re

THREAD_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
"""Safe thread-id shape: one alphanumeric lead, then up to 127 safe chars."""

WIKI_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
"""Kebab-case wiki page slug (matches the wiki schema constraint)."""

_THREAD_ID_RE = re.compile(THREAD_ID_PATTERN)
_WIKI_SLUG_RE = re.compile(WIKI_SLUG_PATTERN)


def validate_thread_id(value: str) -> str:
    """Return a safe thread id or raise.

    Args:
        value: Caller-supplied or derived thread id.

    Returns:
        str: The unchanged, validated thread id.

    Raises:
        ValueError: When the value could escape a store/checkpoint directory.
    """
    if not _THREAD_ID_RE.fullmatch(value):
        raise ValueError("thread_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    return value


def validate_wiki_slug(value: str) -> str:
    """Return a safe wiki slug or raise.

    Args:
        value: Caller-supplied wiki page slug.

    Returns:
        str: The unchanged, validated slug.

    Raises:
        ValueError: When the slug is not kebab-case.
    """
    if not _WIKI_SLUG_RE.fullmatch(value):
        raise ValueError("slug must be kebab-case (^[a-z0-9]+(?:-[a-z0-9]+)*$)")
    return value


__all__ = [
    "THREAD_ID_PATTERN",
    "WIKI_SLUG_PATTERN",
    "validate_thread_id",
    "validate_wiki_slug",
]

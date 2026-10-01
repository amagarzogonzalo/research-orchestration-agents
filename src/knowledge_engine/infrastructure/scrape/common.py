"""Shared scrape helpers — single home for logic used by every adapter.

Keeps the adapter files free of copy-pasted utilities (one definition,
imported everywhere) while staying dependency-free so adapters remain
lazy-importable.
"""

from __future__ import annotations


def fallback_title(url: str) -> str:
    """Derive a human-readable title from a URL when extraction yields none.

    Args:
        url: Page URL.

    Returns:
        str: Last URL path segment, or the full URL when empty.
    """
    return url.rstrip("/").rsplit("/", 1)[-1] or url

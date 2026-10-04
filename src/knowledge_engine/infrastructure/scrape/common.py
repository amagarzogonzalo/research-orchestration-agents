"""Shared scrape helpers — single home for logic used by every adapter.

Keeps the adapter files free of copy-pasted utilities (one definition,
imported everywhere) while staying dependency-free so adapters remain
lazy-importable.
"""

from __future__ import annotations

import httpx

_TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_transient_http(exc: BaseException) -> bool:
    """Whether an HTTP failure is worth retrying (rate limits, timeouts, 5xx).

    Args:
        exc: Caught exception.

    Returns:
        bool: True for retryable failures.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def fallback_title(url: str) -> str:
    """Derive a human-readable title from a URL when extraction yields none.

    Args:
        url: Page URL.

    Returns:
        str: Last URL path segment, or the full URL when empty.
    """
    return url.rstrip("/").rsplit("/", 1)[-1] or url

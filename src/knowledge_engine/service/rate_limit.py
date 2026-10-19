"""In-process sliding-window rate limiter for the HTTP service (issue #30, caveat C).

Single-worker, zero-dependency: a per-key sliding window over the last 60 s.
Covers the "no rate limiting" gap; when ``per_minute <= 0`` it is disabled.
"""

from __future__ import annotations

import time
from collections import deque

_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding-window limiter: at most ``per_minute`` hits per key per minute.

    Args:
        per_minute: Allowed hits per key per 60 s (<= 0 disables the limiter).
    """

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    @property
    def enabled(self) -> bool:
        """Whether the limiter is active.

        Returns:
            bool: True when ``per_minute`` is positive.
        """
        return self._per_minute > 0

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit and report whether it is within the limit.

        Args:
            key: Client key (token or host).
            now: Optional monotonic timestamp (tests); defaults to ``time.monotonic``.

        Returns:
            bool: True when the hit is allowed, False when the limit is exceeded.
        """
        if not self.enabled:
            return True
        moment = time.monotonic() if now is None else now
        window = self._hits.setdefault(key, deque())
        while window and moment - window[0] >= _WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self._per_minute:
            return False
        window.append(moment)
        return True

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        """Seconds until the next hit for ``key`` would be allowed.

        Args:
            key: Client key (token or host).
            now: Optional monotonic timestamp (tests); defaults to ``time.monotonic``.

        Returns:
            float: Seconds to wait (0.0 when a hit would be allowed now).
        """
        if not self.enabled:
            return 0.0
        moment = time.monotonic() if now is None else now
        window = self._hits.get(key)
        if not window:
            return 0.0
        while window and moment - window[0] >= _WINDOW_SECONDS:
            window.popleft()
        if len(window) < self._per_minute:
            return 0.0
        return max(0.0, _WINDOW_SECONDS - (moment - window[0]))


__all__ = ["RateLimiter"]

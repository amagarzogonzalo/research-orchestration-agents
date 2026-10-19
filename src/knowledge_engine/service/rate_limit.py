"""Rate limiters for the HTTP service (issue #30, caveat C).

Two interchangeable implementations:

* :class:`RateLimiter` — zero-dependency, per-process sliding window. It only
  sees traffic on its own worker, so multiple workers need the shared backend.
* :class:`PostgresRateLimiter` — fixed-window counter in the service database,
  shared by every worker and durable across restarts. Keys must already be
  hashed by the caller: raw bearer tokens never reach the database.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60.0
_PRUNE_SECONDS = 60.0
_MAX_KEYS = 10_000
_PRUNE_EVERY = 100


class RateLimiter:
    """Sliding-window limiter: at most ``per_minute`` hits per key per minute.

    Args:
        per_minute: Allowed hits per key per 60 s (<= 0 disables the limiter).
    """

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._last_prune = float("-inf")

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
            key: Client key (hashed token or host).
            now: Optional monotonic timestamp (tests); defaults to ``time.monotonic``.

        Returns:
            bool: True when the hit is allowed, False when the limit is exceeded.
        """
        if not self.enabled:
            return True
        moment = time.monotonic() if now is None else now
        self._prune(moment)
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
            key: Client key (hashed token or host).
            now: Optional monotonic timestamp (tests); defaults to ``time.monotonic``.

        Returns:
            float: Seconds to wait (0.0 when a hit would be allowed now).
        """
        if not self.enabled:
            return 0.0
        moment = time.monotonic() if now is None else now
        self._prune(moment)
        window = self._hits.get(key)
        if not window:
            return 0.0
        while window and moment - window[0] >= _WINDOW_SECONDS:
            window.popleft()
        if len(window) < self._per_minute:
            return 0.0
        return max(0.0, _WINDOW_SECONDS - (moment - window[0]))

    def _prune(self, moment: float) -> None:
        """Drop expired keys and cap the number of tracked keys.

        Args:
            moment: Current monotonic timestamp.
        """
        if moment - self._last_prune >= _PRUNE_SECONDS:
            stale = [key for key, window in self._hits.items() if not window or moment - window[-1] >= _WINDOW_SECONDS]
            for key in stale:
                self._hits.pop(key, None)
            self._last_prune = moment
        while len(self._hits) > _MAX_KEYS:
            oldest = min(self._hits, key=lambda key: self._hits[key][-1] if self._hits[key] else float("-inf"))
            self._hits.pop(oldest, None)


class PostgresRateLimiter:
    """Shared fixed-window limiter backed by the service Postgres database.

    The database clock is authoritative, so workers cannot disagree about the
    window. The limiter degrades open when the database is unreachable: a
    database outage already makes ``/research`` fail, and failing requests with
    a rate-limit error would hide the real failure.

    Args:
        dsn: Postgres connection string (``DATABASE_URL``).
        per_minute: Allowed hits per key per minute (<= 0 disables).
    """

    def __init__(self, dsn: str, per_minute: int) -> None:
        self._dsn = dsn
        self._per_minute = per_minute
        self._calls = 0

    @property
    def enabled(self) -> bool:
        """Whether the limiter is active.

        Returns:
            bool: True when ``per_minute`` is positive.
        """
        return self._per_minute > 0

    def ensure_schema(self) -> None:
        """Create the counter table and prune old windows (idempotent)."""
        import psycopg

        with psycopg.connect(self._dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS service_rate_limits ("
                "  key text NOT NULL,"
                "  window_start timestamptz NOT NULL,"
                "  hits integer NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (key, window_start)"
                ")"
            )
            conn.execute("DELETE FROM service_rate_limits WHERE window_start < now() - interval '1 day'")

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit and report whether it is within the limit.

        Args:
            key: Hashed client key (never a raw bearer token).
            now: Accepted for interface parity; the database clock is used.

        Returns:
            bool: True when the hit is allowed (or the backend is unavailable).
        """
        if not self.enabled:
            return True
        import psycopg

        self._calls += 1
        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                if self._calls % _PRUNE_EVERY == 0:
                    conn.execute("DELETE FROM service_rate_limits WHERE window_start < now() - interval '1 day'")
                row = conn.execute(
                    "INSERT INTO service_rate_limits (key, window_start, hits)"
                    " VALUES (%s, date_trunc('minute', now()), 1)"
                    " ON CONFLICT (key, window_start) DO UPDATE SET hits = service_rate_limits.hits + 1"
                    " RETURNING hits",
                    (key,),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - degrade open, log the real cause
            logger.warning("postgres rate limiter unavailable, allowing request: %s", exc)
            return True
        return int(row[0]) <= self._per_minute if row is not None else True

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        """Seconds until the next hit for ``key`` would be allowed.

        Args:
            key: Hashed client key (never a raw bearer token).
            now: Accepted for interface parity; the database clock is used.

        Returns:
            float: Seconds to wait (0.0 when a hit would be allowed now).
        """
        if not self.enabled:
            return 0.0
        import psycopg

        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                row: Any = conn.execute(
                    "SELECT hits, GREATEST(0, extract(epoch FROM"
                    " (date_trunc('minute', now()) + interval '1 minute' - now())))"
                    " FROM service_rate_limits"
                    " WHERE key = %s AND window_start = date_trunc('minute', now())",
                    (key,),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - retry_after is advisory
            logger.warning("postgres rate limiter unavailable, retry_after=0: %s", exc)
            return 0.0
        if row is None or int(row[0]) < self._per_minute:
            return 0.0
        return float(row[1])


__all__ = ["PostgresRateLimiter", "RateLimiter"]

"""Persisted quota ledger for metered search providers (Tavily/Exa).

Counts successful requests per calendar month (UTC) and remembers when a
provider reported its account limit is spent. The fallback chain checks the
ledger before every call, so an exhausted free tier is skipped without a
network round-trip. Files-only JSON, one file per provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from knowledge_engine.core.exceptions import QuotaExhausted

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current time in UTC.

    Returns:
        datetime: Current UTC timestamp.
    """
    return datetime.now(UTC)


class MonthlyQuotaLedger:
    """Per-provider request counter with month rollover."""

    def __init__(
        self,
        provider: str,
        *,
        limit: int,
        root: str | Path = "research_store/_usage",
        scope: str = "monthly",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create a MonthlyQuotaLedger.

        Args:
            provider: Provider key (used as the file name).
            limit: Requests allowed per period.
            root: Directory holding ledger files.
            scope: ``monthly`` or ``lifetime``.
            clock: Injectable clock for tests; defaults to UTC now.
        """
        self.provider = provider
        self.limit = limit
        self.root = Path(root)
        self.scope = scope
        self._clock = clock or _utcnow
        self._lock = asyncio.Lock()

    def path_for(self) -> Path:
        """Return the ledger file path.

        Returns:
            Path: ``<root>/<provider>.json``.
        """
        return self.root / f"{self.provider}.json"

    def _period(self) -> str:
        """Return the current quota period key.

        Returns:
            str: ``lifetime`` or ``YYYY-MM`` in UTC.
        """
        if self.scope == "lifetime":
            return "lifetime"
        return self._clock().strftime("%Y-%m")

    def _load(self) -> dict:
        """Load the ledger, resetting it when the period has rolled over.

        Returns:
            dict: Ledger state for the current period.
        """
        period = self._period()
        path = self.path_for()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("period") == period:
                    return data
            except (OSError, ValueError) as exc:
                logger.warning("quota ledger read failed for %s: %s", path, exc)
        return {"period": period, "used": 0, "limit": self.limit, "exhausted": False}

    def _save(self, data: dict) -> None:
        """Persist the ledger atomically.

        Args:
            data: Ledger state to write.
        """
        path = self.path_for()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    async def available(self) -> bool:
        """Whether a request may still be made this period.

        Returns:
            bool: True when not exhausted and under the limit.
        """
        data = self._load()
        return not data.get("exhausted", False) and data.get("used", 0) < self.limit

    async def check(self) -> None:
        """Raise when the quota is exhausted.

        Raises:
            QuotaExhausted: When no requests remain this period.
        """
        if not await self.available():
            raise QuotaExhausted(f"{self.provider} free quota exhausted for {self._period()}")

    async def record(self, cost: int = 1, *, exhausted: bool = False) -> None:
        """Add a request to the ledger (read-modify-write under a lock).

        Args:
            cost: Requests to add.
            exhausted: Force the exhausted flag (provider reported a hard limit).
        """
        async with self._lock:
            data = self._load()
            data["limit"] = self.limit
            data["used"] = data.get("used", 0) + cost
            if exhausted or data["used"] >= self.limit:
                data["exhausted"] = True
            self._save(data)

    async def snapshot(self) -> dict:
        """Return a read-only copy of the current ledger state.

        Returns:
            dict: Ledger state for the current period.
        """
        return dict(self._load())

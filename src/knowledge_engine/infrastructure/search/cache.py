"""Disk cache for general-web search results (protects metered quotas).

A files-only cache keyed on a normalized query. Hits cost nothing, so a repeat
query never spends a Tavily/Exa credit or re-hammers the keyless DuckDuckGo
endpoint. Entries are JSON under ``<root>/<sha256>.json`` with an mtime-based
TTL; empty result sets are never cached, so a transient miss cannot freeze.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)

_NON_WORD = re.compile(r"[^a-z0-9]+")
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600


def normalize_query(query: str) -> str:
    """Normalize a query so trivial formatting differences share one entry.

    Args:
        query: Raw query string.

    Returns:
        str: Lowercased, punctuation-stripped, whitespace-collapsed query.
    """
    return _NON_WORD.sub(" ", query.lower()).strip()


class SearchCache:
    """Files-only search cache with a TTL."""

    def __init__(
        self,
        root: str | Path = "research_store/_cache/search",
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """Create a SearchCache.

        Args:
            root: Directory holding cache entries.
            ttl_seconds: Entry lifetime in seconds.
        """
        self.root = Path(root)
        self.ttl_seconds = ttl_seconds

    def path_for(self, query: str) -> Path:
        """Return the cache file path for a query.

        Args:
            query: Raw query string.

        Returns:
            Path: ``<root>/<sha256(normalized query)>.json``.
        """
        key = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()
        return self.root / f"{key}.json"

    async def get(self, query: str) -> list[SourceRecord] | None:
        """Return cached records for a query, or None on a miss/expiry.

        Args:
            query: Raw query string.

        Returns:
            list[SourceRecord] | None: Cached records, or None.
        """
        path = self.path_for(query)
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > self.ttl_seconds:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records = [SourceRecord.model_validate(item) for item in payload.get("records", [])]
        except (OSError, ValueError) as exc:
            logger.warning("search cache read failed for %s: %s", path, exc)
            return None
        return records or None

    async def set(self, query: str, records: list[SourceRecord]) -> None:
        """Persist records for a query (no-op for an empty result set).

        Args:
            query: Raw query string.
            records: Records to cache.
        """
        if not records:
            return
        path = self.path_for(query)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": time.time(),
            "query": query,
            "records": [record.model_dump(mode="json") for record in records],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


class CachedSearchAdapter(SearchPort):
    """Wrap a search adapter with the disk cache (read-through)."""

    def __init__(self, inner: SearchPort, cache: SearchCache) -> None:
        """Create a CachedSearchAdapter.

        Args:
            inner: Search adapter to wrap.
            cache: Backing cache.
        """
        self.inner = inner
        self.cache = cache
        self.name = f"cached:{getattr(inner, 'name', 'search')}"

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Return cached results if fresh, else fetch and cache them.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved sources.
        """
        cached = await self.cache.get(query)
        if cached is not None:
            logger.debug("search cache hit for %r", query)
            return cached[:limit]
        records = await self.inner.search(query, limit=limit)
        if records:
            await self.cache.set(query, records)
        return records

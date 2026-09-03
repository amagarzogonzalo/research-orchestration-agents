"""Federated demux: fan search/fetch across multiple sources (ADR 0001/0004).

Queries each configured source and merges by DOI/id, applying a bounded
concurrency so shared quotas (Semantic Scholar) aren't burst-fired.
"""

from __future__ import annotations

import asyncio
import logging

from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)


class DemuxSearchAdapter(SearchPort, FetchPort):
    def __init__(self, sources: list, *, max_concurrency: int = 2) -> None:
        self.sources = sources
        self._sem = asyncio.Semaphore(max_concurrency)

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        async def run(src: SearchPort) -> list[SourceRecord]:
            async with self._sem:
                try:
                    return await src.search(query, limit=limit)
                except Exception as exc:  # noqa: BLE001 - one source must not sink the fan-out
                    logger.warning("search source %s failed: %s", getattr(src, "name", src), exc)
                    return []

        results = await asyncio.gather(*(run(s) for s in self.sources))
        return _merge(results)

    async def fetch(self, doi: str) -> SourceRecord | None:
        async def run(src: FetchPort):
            async with self._sem:
                try:
                    return await src.fetch(doi)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("fetch source %s failed for %s: %s", getattr(src, "name", src), doi, exc)
                    return None

        found = await asyncio.gather(*(run(s) for s in self.sources if hasattr(s, "fetch")))
        for record in _merge([f for f in found if f is not None]):
            return record
        return None


def _merge(chunks: list[list[SourceRecord] | list[SourceRecord]]) -> list[SourceRecord]:
    """Dedupe by DOI then id, preserving order."""
    seen: set[str] = set()
    merged: list[SourceRecord] = []
    for chunk in chunks:
        for rec in chunk:
            key = rec.doi or rec.id
            if key not in seen:
                seen.add(key)
                merged.append(rec)
    return merged

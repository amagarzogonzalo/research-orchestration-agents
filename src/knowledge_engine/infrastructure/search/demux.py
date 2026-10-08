"""Federated demux: fan search/fetch across multiple sources (ADR 0001/0004).

Queries each configured source and merges by DOI/id, applying a bounded
concurrency so shared quotas (Semantic Scholar) aren't burst-fired.
"""

from __future__ import annotations

import asyncio
import logging
import re

from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.observability import telemetry
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)


class DemuxSearchAdapter(SearchPort, FetchPort):
    def __init__(self, sources: list, *, max_concurrency: int = 2) -> None:
        """Create a DemuxSearchAdapter.

        Args:
            sources: Resolved source records.
            max_concurrency: Maximum concurrency.

        """
        self.sources = sources
        self._sem = asyncio.Semaphore(max_concurrency)

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search scholarly sources.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved sources.

        """

        async def run(src: SearchPort) -> list[SourceRecord]:
            """Run the unit of work.

            Args:
                src: Source record.

            Returns:
                list[SourceRecord]: Resolved sources.

            """
            async with self._sem:
                try:
                    return await src.search(query, limit=limit)
                except Exception as exc:  # noqa: BLE001 - one source must not sink the fan-out
                    logger.warning("search source %s failed: %s", getattr(src, "name", src), exc)
                    return []

        with telemetry.span("search.federated", query=query) as current:
            results = await asyncio.gather(*(run(s) for s in self.sources))
            merged = _merge(results)
            current.set_attribute("sources_found", len(merged))
            return merged

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Fetch a record.

        Args:
            doi: Digital object identifier.

        Returns:
            SourceRecord | None: Record or None when unknown.

        """

        async def run(src: FetchPort) -> SourceRecord | None:
            """Fetch one source with isolation.

            Args:
                src: Fetch-capable source.

            Returns:
                SourceRecord | None: Fetched record or None on failure.
            """
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


def _norm_title(title: str) -> str:
    """Normalize a title for duplicate detection.

    Args:
        title: Raw title.

    Returns:
        str: Lowercased alphanumeric-only title.
    """
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _merge(chunks: list[list[SourceRecord] | list[SourceRecord]]) -> list[SourceRecord]:
    """Dedupe by DOI, then id, then normalized title, preserving order.

    Title dedupe catches the same paper registered under different DOIs
    (common with cross-registrar preprints). Titles shorter than 30
    normalized chars never dedupe to avoid false positives on generic
    titles. First record wins (source priority order).

    Args:
        chunks: Result chunks to merge.

    Returns:
        list[SourceRecord]: Resolved sources.

    """
    seen: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[SourceRecord] = []
    for chunk in chunks:
        for rec in chunk:
            key = rec.doi or rec.id
            if key in seen:
                continue
            title_key = _norm_title(rec.title)
            if len(title_key) >= 30 and title_key in seen_titles:
                logger.info("demux dropping title-duplicate %s (%s)", rec.id, rec.title[:60])
                continue
            seen.add(key)
            if len(title_key) >= 30:
                seen_titles.add(title_key)
            merged.append(rec)
    return merged

"""dblp adapter — CS bibliography with venue metadata (keyless JSON API).

``/search/publ/api`` returns publications with title, authors, year, DOI and
venue — the "CS papers, precisely cited" signal. Mapping is clean and stable.

Live-probe caveat 2026-10-01: fetching the API from this environment returned
the Anubis bot-check page instead of JSON, so the live path is UNVERIFIED from
here — treat the first live run as the real probe. Per-source failure
isolation in the demux means a blocked dblp degrades silently to the other
sources. Parser and offline tests below follow the long-stable documented
schema defensively (``author`` may be a string or a list).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://dblp.org/search/publ/api"


class DblpAdapter(SearchPort):
    """Computer-science bibliography search (free, keyless)."""

    name = "dblp"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _authors(info: dict) -> list[str]:
        raw = (info.get("authors") or {}).get("author", [])
        if isinstance(raw, str):
            return [raw]
        return [str(a) for a in raw if a]

    @staticmethod
    def _to_record(hit: dict) -> SourceRecord:
        info = hit.get("info", {})
        try:
            year = int(str(info.get("year") or "")[:4])
        except ValueError:
            year = None
        ee = str(info.get("ee") or "")
        return SourceRecord(
            id=f"dblp:{hit.get('@key', '') or info.get('doi') or info.get('title', '')[:40]}",
            doi=info.get("doi") or None,
            title=info.get("title") or "",
            source=SearchSource.DBLP,
            authors=DblpAdapter._authors(info),
            year=year,
            abstract=None,
            pdf_url=ee if ee.lower().endswith(".pdf") else None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Keyword search over the CS bibliography.

        Args:
            query: Search terms.
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Records in dblp relevance order.

        Raises:
            SearchError: If the API call fails.
        """
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params={"q": query, "format": "json", "h": limit})
                resp.raise_for_status()
                hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
                if isinstance(hits, dict):
                    hits = [hits]
                return [self._to_record(hit) for hit in hits[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"dblp search failed for {query!r}: {exc}") from exc

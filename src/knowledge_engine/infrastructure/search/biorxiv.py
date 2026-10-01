"""bioRxiv / medRxiv adapter — life-science preprints beyond arXiv.

One class serves both servers (same API shape, different base): instantiate
once per server. The API offers no keyword search — only date-range browsing
and DOI lookup — so ``search`` browses a recent window and filters
title+abstract client-side on every query token, while ``fetch`` resolves a
DOI directly. Both behaviors are deliberate and documented, not forced.

Live probes 2026-10-01: ``/details/biorxiv/2026-09-01/2026-09-15/0`` and the
medRxiv equivalent both HTTP 200 with a ``collection`` of records carrying
``doi`` / ``title`` / ``authors`` ("Last, F.; ..." string) / ``date`` /
``abstract`` / ``category`` / ``server``. Bare ``/details/biorxiv/0/1/0``
returns an error payload (dates must be yyyy-mm-dd), so the adapter always
sends real dates.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

PreprintServer = Literal["biorxiv", "medrxiv"]

_PAGE_SIZE = 30
_MAX_PAGES = 2


class BiorxivAdapter(SearchPort, FetchPort):
    """Preprint search over the bioRxiv/medRxiv API (free, keyless)."""

    name = "biorxiv"

    def __init__(self, server: PreprintServer = "biorxiv", *, timeout: float = 15.0) -> None:
        self.server: PreprintServer = server
        self._timeout = timeout
        self.name = server

    @property
    def _source(self) -> SearchSource:
        return SearchSource.BIORXIV if self.server == "biorxiv" else SearchSource.MEDRXIV

    def _base(self) -> str:
        return f"https://api.{self.server}.org/details/{self.server}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _to_record(item: dict, source: SearchSource) -> SourceRecord:
        authors = [a.strip() for a in str(item.get("authors") or "").split(";") if a.strip()]
        date = str(item.get("date") or "")
        year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
        doi = str(item.get("doi") or "") or None
        return SourceRecord(
            id=f"{source.value}:{doi}" if doi else f"{source.value}:{item.get('title', '')[:60]}",
            doi=doi,
            title=item.get("title") or "",
            source=source,
            authors=authors,
            year=year,
            abstract=item.get("abstract"),
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _matches(item: dict, tokens: list[str]) -> bool:
        """Every substantive query token must appear in title or abstract."""
        haystack = f"{item.get('title') or ''}\n{item.get('abstract') or ''}".lower()
        return all(t in haystack for t in tokens)

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Browse the recent window and keep records matching every query token.

        The API has no keyword search, so this pages the date-range endpoint
        (bounded to ``_MAX_PAGES``) and filters client-side.

        Args:
            query: Search terms (tokenized client-side; terms under 3 chars ignored).
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Matching preprints, most recent window first.

        Raises:
            SearchError: If the API calls fail.
        """
        tokens = [t.lower() for t in query.split() if len(t) > 2]
        end = datetime.now(UTC).date()
        start = end - timedelta(days=90)
        hits: list[SourceRecord] = []
        seen: set[str] = set()
        try:
            async with self._client() as client:
                cursor = 0
                for _ in range(_MAX_PAGES):
                    url = f"{self._base()}/{start.isoformat()}/{end.isoformat()}/{cursor}"
                    resp = await client.get(url)
                    resp.raise_for_status()
                    items = resp.json().get("collection", [])
                    if not items:
                        break
                    for item in items:
                        if not self._matches(item, tokens):
                            continue
                        rec = self._to_record(item, self._source)
                        if rec.id in seen:
                            continue
                        seen.add(rec.id)
                        hits.append(rec)
                        if len(hits) >= limit:
                            return hits
                    cursor += _PAGE_SIZE
        except httpx.HTTPError as exc:
            raise SearchError(f"bioRxiv/medRxiv search failed for {query!r}: {exc}") from exc
        return hits

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Resolve a DOI directly via the per-DOI details endpoint.

        Args:
            doi: DOI to resolve (bare, without URL prefix).

        Returns:
            SourceRecord | None: The resolved record, or None when this server does not host it.

        Raises:
            SearchError: If the API call fails.
        """
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base()}/{doi}")
                resp.raise_for_status()
                items = resp.json().get("collection", [])
                if not items:
                    return None
                return self._to_record(items[0], self._source)
        except httpx.HTTPError as exc:
            raise SearchError(f"bioRxiv/medRxiv fetch failed for {doi}: {exc}") from exc

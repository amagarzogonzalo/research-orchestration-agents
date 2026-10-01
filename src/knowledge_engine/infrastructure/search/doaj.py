"""DOAJ adapter — Directory of Open Access Journals article search (keyless).

``/api/search/articles`` returns OA articles with ``bibjson`` metadata: title,
authors, year (as a string), DOI identifiers and full-text links. Everything
here is open access by construction, so resolved records feed the scrape tier
without paywall risk.

Live probe 2026-10-01: ``/api/search/articles/attention%20transformer``
HTTP 200 with ``results[].bibjson`` carrying ``title`` / ``author[{name}]`` /
``year`` / ``identifier[{type: doi}]`` / ``link[{type: fulltext}]`` /
``abstract``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://doaj.org/api/search/articles"


class DoajAdapter(SearchPort):
    """Open-access article search over DOAJ (free, keyless)."""

    name = "doaj"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        bib = item.get("bibjson", {})
        authors = [a.get("name", "") for a in bib.get("author", []) if a.get("name")]
        doi: str | None = None
        for ident in bib.get("identifier", []):
            if ident.get("type") == "doi" and ident.get("id"):
                doi = str(ident["id"])
                break
        fulltext: str | None = None
        for link in bib.get("link", []):
            if link.get("type") == "fulltext" and link.get("url"):
                fulltext = str(link["url"])
                break
        try:
            year = int(str(bib.get("year") or "")[:4])
        except ValueError:
            year = None
        return SourceRecord(
            id=f"doaj:{item.get('id', '')}",
            doi=doi,
            title=bib.get("title") or "",
            source=SearchSource.DOAJ,
            authors=authors,
            year=year,
            abstract=bib.get("abstract"),
            pdf_url=fulltext,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Keyword search over OA journal articles.

        Args:
            query: Search terms.
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Records in DOAJ relevance order.

        Raises:
            SearchError: If the API call fails.
        """
        try:
            async with self._client() as client:
                resp = await client.get(f"{_BASE}/{query}", params={"pageSize": limit})
                resp.raise_for_status()
                items = resp.json().get("results", [])
                return [self._to_record(item) for item in items[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"DOAJ search failed for {query!r}: {exc}") from exc

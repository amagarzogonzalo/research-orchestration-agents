"""Europe PMC adapter — biomedical literature + OA full-text links (EBI).

``/rest/search`` returns records with DOI, abstract, citation counts and a
``fullTextUrlList`` whose ``documentStyle`` distinguishes PDF from HTML. Only
genuine ``pdf`` URLs are stored as ``pdf_url``; HTML full text stays reachable
through the Europe PMC article page, which ``_derive_url`` prefers (see
``orchestration/nodes/bodies.py``).

Live probe 2026-10-01: ``/rest/search?query=attention+transformer&format=json``
HTTP 200 with ``resultList.result[]`` carrying ``id`` / ``source`` / ``title`` /
``authorString`` / ``pubYear`` / ``doi`` / ``citedByCount`` / ``inPMC`` /
``fullTextUrlList``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcAdapter(SearchPort, FetchPort):
    """Biomedical search over Europe PMC (free, keyless).

    Attributes:
        name: Attribute description.
    """

    name = "europe_pmc"

    def __init__(self, *, timeout: float = 15.0) -> None:
        """Create a EuropePmcAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.


        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _pdf_url(item: dict) -> str | None:
        """First full-text URL explicitly marked as PDF, else None.

        Args:
            item: Item.

        Returns:
            str | None: Text or None.

        """
        ft = item.get("fullTextUrlList") or {}
        for entry in ft.get("fullTextUrl", []):
            if entry.get("documentStyle") == "pdf" and entry.get("url"):
                return str(entry["url"])
        return None

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            item: Item.

        Returns:
            SourceRecord: Resolved source record.


        """
        ext_id = str(item.get("id") or "")
        src = str(item.get("source") or "MED")
        authors = [a.strip() for a in str(item.get("authorString") or "").split(",") if a.strip()]
        try:
            year = int(str(item.get("pubYear") or "")[:4])
        except ValueError:
            year = None
        try:
            cited = int(item.get("citedByCount") or 0)
        except (TypeError, ValueError):
            cited = 0
        return SourceRecord(
            id=f"europe_pmc:{src}:{ext_id}",
            doi=item.get("doi") or None,
            title=item.get("title") or "",
            source=SearchSource.EUROPE_PMC,
            authors=authors,
            year=year,
            abstract=item.get("abstractText"),
            pdf_url=EuropePmcAdapter._pdf_url(item),
            citation_count=cited,
            venue=((item.get("journalInfo") or {}).get("journal") or {}).get("title") or None,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Keyword search over Europe PMC (Boolean syntax supported).

        Args:
            query: Search terms.
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Records in Europe PMC relevance order.

        Raises:
            SearchError: If the API call fails.
        """
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params={"query": query, "format": "json", "pageSize": limit})
                resp.raise_for_status()
                items = resp.json().get("resultList", {}).get("result", [])
                return [self._to_record(item) for item in items[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"Europe PMC search failed for {query!r}: {exc}") from exc

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Resolve a DOI via a DOI-tagged query.

        Args:
                    doi: DOI to resolve (bare, without URL prefix).


        Returns:
                    SourceRecord | None: The resolved record, or None when Europe PMC does not index it.
        """
        records = await self.search(f'DOI:"{doi}"', limit=1)
        return records[0] if records else None

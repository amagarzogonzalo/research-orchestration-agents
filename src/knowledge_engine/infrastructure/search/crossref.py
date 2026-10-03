"""Crossref — DOI-centric metadata backbone for citation validation."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_BASE = "https://api.crossref.org"


class CrossrefAdapter(SearchPort, FetchPort):
    """CrossrefAdapter.

    Attributes:
        name: Attribute description."""

    name = "crossref"

    def __init__(self, mailto: str, *, timeout: float = 15.0) -> None:
        """Create a CrossrefAdapter.

        Args:
            mailto: Contact email for API etiquette.
            timeout: Request timeout in seconds.

        """
        self.mailto = mailto
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.


        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": f"knowledge-engine/{self.mailto}"})

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            item: Item.

        Returns:
            SourceRecord: Resolved source record.


        """
        title = (item.get("title") or [""])[0]
        authors = []
        for a in item.get("author", []):
            name = (a.get("given", "") + " " + a.get("family", "")).strip()
            if name:
                authors.append(name)
        year = None
        date_parts = (item.get("issued", {}).get("date-parts") or [[None]])[0]
        if date_parts and date_parts[0]:
            year = date_parts[0]
        return SourceRecord(
            id=f"crossref:{item.get('DOI', '')}",
            doi=item.get("DOI"),
            title=title,
            source=SearchSource.CROSSREF,
            authors=authors,
            year=year,
            abstract=item.get("abstract"),
            citation_count=item.get("is-referenced-by-count", 0),
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Fetch a record.

        Args:
            doi: Digital object identifier.

        Returns:
            SourceRecord | None: Record or None when unknown.

        Raises:
            SearchError: When the operation fails.

        """
        url = f"{_BASE}/works/{doi}"
        try:
            async with self._client() as client:
                resp = await client.get(url, params={"mailto": self.mailto})
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._to_record(resp.json()["message"])
        except httpx.HTTPError as exc:
            raise SearchError(f"Crossref fetch failed for {doi}: {exc}") from exc

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search scholarly sources.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved sources.

        Raises:
            SearchError: When the operation fails.

        """
        url = f"{_BASE}/works"
        params = {"query": query, "rows": limit, "mailto": self.mailto}
        try:
            async with self._client() as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return [self._to_record(item) for item in resp.json()["message"]["items"]]
        except httpx.HTTPError as exc:
            raise SearchError(f"Crossref search failed for {query!r}: {exc}") from exc

"""OpenAlex adapter.

Per ADR 0002/0000: single-record lookups by DOI/ID are *free and un-metered*
(`fetch`); only full search consumes daily credits. Prefer DOI/ID-first, fall
back to search, and always pass `mailto` for polite-pool quota.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_BASE = "https://api.openalex.org"


class OpenAlexAdapter(SearchPort, FetchPort):
    """OpenAlexAdapter.

    Attributes:
        name: Attribute description."""

    name = "openalex"

    def __init__(self, mailto: str, *, timeout: float = 15.0) -> None:
        """Create a OpenAlexAdapter.

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
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in item.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ]
        best_oa = item.get("best_oa_location") or {}
        abstract = item.get("abstract_inverted_index")
        abstract_text = _inverted_to_text(abstract) if abstract else None
        return SourceRecord(
            id=f"openalex:{item.get('id', '').rsplit('/', 1)[-1]}",
            doi=(item.get("doi") or "").replace("https://doi.org/", "") or None,
            title=item.get("display_name") or item.get("title") or "",
            source=SearchSource.OPENALEX,
            authors=authors,
            year=item.get("publication_year"),
            abstract=abstract_text,
            pdf_url=best_oa.get("pdf_url"),
            citation_count=item.get("cited_by_count", 0),
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Free single-record DOI lookup (does not consume daily credits).

        Args:
            doi: Digital object identifier.

        Returns:
            SourceRecord | None: Record or None when unknown.

        Raises:
            SearchError: When the operation fails.

        """
        url = f"{_BASE}/works/doi:{doi}"
        try:
            async with self._client() as client:
                resp = await client.get(url, params={"mailto": self.mailto})
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._to_record(resp.json())
        except httpx.HTTPError as exc:
            raise SearchError(f"OpenAlex fetch failed for {doi}: {exc}") from exc

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
        params = {
            "filter": f"title.search:{query}",
            "per-page": limit,
            "mailto": self.mailto,
        }
        try:
            async with self._client() as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return [self._to_record(item) for item in resp.json().get("results", [])]
        except httpx.HTTPError as exc:
            raise SearchError(f"OpenAlex search failed for {query!r}: {exc}") from exc


def _inverted_to_text(inverted: dict) -> str:
    """Rebuild an abstract from OpenAlex's inverted-position index.

    Args:
        inverted: Whether the index is inverted.

    Returns:
        str: Result text.


    """
    if not inverted:
        return ""
    # Values are lists of integer positions (e.g. {"Deep": [0, 98]}); max()
    # over the raw values would compare lists, not positions (TypeError
    # downstream when sizing the word buffer).
    positions = [pos for locs in inverted.values() if isinstance(locs, list) for pos in locs if isinstance(pos, int)]
    words: list[str] = [""] * (max(positions, default=-1) + 1)
    for word, locs in inverted.items():
        if not isinstance(locs, list):
            continue
        for pos in locs:
            if isinstance(pos, int) and 0 <= pos < len(words):
                words[pos] = word
    return " ".join(words).strip()

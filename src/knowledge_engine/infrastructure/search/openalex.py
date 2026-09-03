"""OpenAlex adapter.

Per ADR 0002/0000: single-record lookups by DOI/ID are *free and un-metered*
(`fetch`); only full search consumes daily credits. Prefer DOI/ID-first, fall
back to search, and always pass `mailto` for polite-pool quota.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_BASE = "https://api.openalex.org"


class OpenAlexAdapter(SearchPort, FetchPort):
    name = "openalex"

    def __init__(self, mailto: str, *, timeout: float = 15.0) -> None:
        self.mailto = mailto
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": f"knowledge-engine/{self.mailto}"})

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
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
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Free single-record DOI lookup (does not consume daily credits)."""
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
    """Rebuild an abstract from OpenAlex's inverted-position index."""
    if not inverted:
        return ""
    length = max(inverted.values())
    words: list[str] = [""] * (length + 1)
    for word, positions in inverted.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words).strip()
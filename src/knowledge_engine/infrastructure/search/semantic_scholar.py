"""Semantic Scholar adapter.

Shared public limit is 1k req/s — a 429 magnet (observed live during planning).
Callers must route through the token bucket (ADR 0003) and circuit breaker
(ADR 0004); this adapter should never be burst-fired directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from knowledge_engine.core.exceptions import ProviderUnavailable, SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_BASE = "https://api.semanticscholar.org/graph/v1"


class SemanticScholarAdapter(SearchPort, FetchPort):
    name = "semantic_scholar"

    def __init__(self, api_key: str | None = None, *, timeout: float = 15.0) -> None:
        self.api_key = api_key
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        headers = {"User-Agent": "knowledge-engine"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return httpx.AsyncClient(timeout=self._timeout, headers=headers)

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
        return SourceRecord(
            id=f"semantic_scholar:{item.get('paperId', '')}",
            doi=item.get("externalIds", {}).get("DOI"),
            title=item.get("title", ""),
            source=SearchSource.SEMANTIC_SCHOLAR,
            authors=authors,
            year=item.get("year"),
            abstract=item.get("abstract"),
            pdf_url=item.get("openAccessPdf", {}).get("url"),
            citation_count=item.get("citationCount", 0),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    async def fetch(self, doi: str) -> SourceRecord | None:
        url = f"{_BASE}/paper/DOI:{doi}"
        params = {
            "fields": "paperId,title,year,authors,abstract,citationCount,externalIds,openAccessPdf"
        }
        try:
            async with self._client() as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    raise ProviderUnavailable("Semantic Scholar rate limited (429)")
                resp.raise_for_status()
                return self._to_record(resp.json())
        except httpx.HTTPError as exc:
            raise SearchError(f"Semantic Scholar fetch failed for {doi}: {exc}") from exc

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        url = f"{_BASE}/paper/search"
        params = {
            "query": query,
            "limit": limit,
            "fields": "paperId,title,year,authors,abstract,citationCount,externalIds,openAccessPdf",
        }
        try:
            async with self._client() as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    raise ProviderUnavailable("Semantic Scholar rate limited (429)")
                resp.raise_for_status()
                return [self._to_record(item) for item in resp.json().get("data", [])]
        except httpx.HTTPError as exc:
            raise SearchError(f"Semantic Scholar search failed for {query!r}: {exc}") from exc
"""Google Custom Search adapter — general-web fallback (keyed).

Custom Search JSON API: 100 free queries/day, then billed. Requires a
``GOOGLE_API_KEY`` plus a Programmable Search Engine ``GOOGLE_CX_ENGINE`` id;
the engine wires this adapter only when both are present. NOTE: Google flags
CSE as unavailable for new customers with discontinuation Jan 1, 2027 — if a
CX engine cannot be created, use the Serper fallback instead (same Google
results via reseller, one-time free credit, no card).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://www.googleapis.com/customsearch/v1"


class GoogleCSEAdapter(SearchPort):
    """General-web search via Google Custom Search (keyed, metered).

    Attributes:
        name: Adapter name.
    """

    name = "google_cse"

    def __init__(self, api_key: str, cx: str, *, timeout: float = 15.0) -> None:
        """Create a GoogleCSEAdapter.

        Args:
            api_key: Google API key.
            cx: Programmable Search Engine id.
            timeout: Request timeout in seconds.
        """
        self.api_key = api_key
        self.cx = cx
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.
        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            item: Custom Search result item.

        Returns:
            SourceRecord: Resolved source record.
        """
        link = item.get("link") or ""
        return SourceRecord(
            id=f"google:{link}",
            doi=None,
            title=item.get("title") or "",
            source=SearchSource.GOOGLE,
            authors=[],
            year=None,
            abstract=item.get("snippet") or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Web search via Google Custom Search.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in Google relevance order.

        Raises:
            SearchError: If the API call fails.
        """
        params = {"key": self.api_key, "cx": self.cx, "q": query, "num": min(limit, 10)}
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params=params)
                resp.raise_for_status()
                items = resp.json().get("items", [])
                return [self._to_record(item) for item in items[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"Google CSE search failed for {query!r}: {exc}") from exc

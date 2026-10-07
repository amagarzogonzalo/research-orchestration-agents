"""Tavily adapter — general-web search for agents (keyed, metered).

Free tier: 1,000 search credits/month, no card (basic depth = 1 credit).
Requests POST ``https://api.tavily.com/search`` with a Bearer key. The quota
ledger is checked before the call and updated after; HTTP 429/432/433 (rate /
key / plan limit) marks the period exhausted so the fallback chain skips it
without retrying.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_ENDPOINT = "https://api.tavily.com/search"
_QUOTA_STATUS = frozenset({429, 432, 433})
TAVILY_MONTHLY_LIMIT = 1000


class TavilyAdapter(SearchPort):
    """General-web search via Tavily (metered; falls through the chain).

    Attributes:
        name: Adapter name.
    """

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        ledger: MonthlyQuotaLedger | None = None,
        timeout: float = 20.0,
        limit: int = TAVILY_MONTHLY_LIMIT,
    ) -> None:
        """Create a TavilyAdapter.

        Args:
            api_key: Tavily API key (``tvly-...``).
            ledger: Quota ledger; a monthly one is built when omitted.
            timeout: Request timeout in seconds.
            limit: Monthly request cap used when building the default ledger.
        """
        self.api_key = api_key
        self._timeout = timeout
        self.ledger = ledger or MonthlyQuotaLedger("tavily", limit=limit)

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client with Bearer auth.
        """
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        return httpx.AsyncClient(timeout=self._timeout, headers=headers)

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert a Tavily result to a record.

        Args:
            item: Tavily result object.

        Returns:
            SourceRecord: Resolved source record.
        """
        url = item.get("url") or ""
        return SourceRecord(
            id=f"tavily:{url}",
            doi=None,
            title=item.get("title") or "",
            source=SearchSource.TAVILY,
            authors=[],
            year=None,
            abstract=item.get("content") or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Web search via Tavily.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in Tavily relevance order.

        Raises:
            QuotaExhausted: When the free quota is spent.
            SearchError: If the request fails.
        """
        await self.ledger.check()
        payload = {"query": query, "max_results": max(1, min(limit, 20)), "search_depth": "basic"}
        try:
            async with self._client() as client:
                resp = await client.post(_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily search failed for {query!r}: {exc}") from exc
        if resp.status_code in _QUOTA_STATUS:
            await self.ledger.record(exhausted=True)
            raise QuotaExhausted(f"Tavily quota exhausted (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise SearchError(f"Tavily search failed for {query!r}: HTTP {resp.status_code}")
        items = resp.json().get("results", [])
        await self.ledger.record()
        return [self._to_record(item) for item in items[:limit]]

"""Serper adapter — independent Google-grade general-web fallback (keyed, metered).

Free tier: 2,500 searches/month, no card. Requests POST
``https://google.serper.dev/search`` with an ``X-API-KEY`` header. Sits in the
sequential fallback chain as the vendor-disjoint safety net: Tavily and Exa
share one vendor family, so a DDG bot-wall plus a Tavily/Exa outage cannot
kill the web tier while Serper is configured (issue #21).

HTTP 429 marks the period exhausted so the chain skips it without retrying;
401/403/5xx surface as :class:`SearchError` (non-quota; the chain's breaker
isolates repeat failures).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_ENDPOINT = "https://google.serper.dev/search"
_QUOTA_STATUS = frozenset({429})
SERPER_MONTHLY_LIMIT = 2500


class SerperAdapter(SearchPort):
    """Google-grade web search via Serper (metered; falls through the chain).

    Attributes:
        name: Adapter name.
    """

    name = "serper"

    def __init__(
        self,
        api_key: str,
        *,
        ledger: MonthlyQuotaLedger | None = None,
        timeout: float = 20.0,
        limit: int = SERPER_MONTHLY_LIMIT,
    ) -> None:
        """Create a SerperAdapter.

        Args:
            api_key: Serper API key.
            ledger: Quota ledger; a monthly one is built when omitted.
            timeout: Request timeout in seconds.
            limit: Monthly request cap used when building the default ledger.
        """
        self.api_key = api_key
        self._timeout = timeout
        self.ledger = ledger or MonthlyQuotaLedger("serper", limit=limit)

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client with X-API-KEY auth.
        """
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        return httpx.AsyncClient(timeout=self._timeout, headers=headers)

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert a Serper organic hit to a record.

        Args:
            item: Serper organic result object.

        Returns:
            SourceRecord: Resolved source record.
        """
        url = item.get("link") or ""
        return SourceRecord(
            id=f"serper:{url}",
            doi=None,
            title=item.get("title") or "",
            source=SearchSource.SERPER,
            authors=[],
            year=None,
            abstract=item.get("snippet") or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Web search via Serper.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in Google relevance order (empty when
                the query has no organic hits).

        Raises:
            QuotaExhausted: When the free quota is spent.
            SearchError: If the request fails.
        """
        await self.ledger.check()
        payload = {"q": query, "num": max(1, min(limit, 20))}
        try:
            async with self._client() as client:
                resp = await client.post(_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            raise SearchError(f"Serper search failed for {query!r}: {exc}") from exc
        if resp.status_code in _QUOTA_STATUS:
            await self.ledger.record(exhausted=True)
            raise QuotaExhausted(f"Serper quota exhausted (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise SearchError(f"Serper search failed for {query!r}: HTTP {resp.status_code}")
        items = resp.json().get("organic", [])
        await self.ledger.record()
        return [self._to_record(item) for item in items[:limit]]


__all__ = ["SERPER_MONTHLY_LIMIT", "SerperAdapter"]

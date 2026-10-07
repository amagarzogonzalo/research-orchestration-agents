"""Exa adapter — neural/semantic web search (keyed, metered).

Free tier: $10 of credits/month that reset on the 1st, no card. Searches POST
``https://api.exa.ai/search`` with an ``x-api-key`` header. ``type=instant``
is the cheapest mode and highlights give a citation-ready snippet. A 402 (out
of credits) or 429 marks the period exhausted so the chain skips it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import QuotaExhausted, SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_ENDPOINT = "https://api.exa.ai/search"
_QUOTA_STATUS = frozenset({402, 429})
# Conservative request cap: the $10/mo credit buys ~2,500 instant searches
# ($4/1k), but highlights/latency vary, so we stop well short of the ceiling.
EXA_MONTHLY_LIMIT = 1000


class ExaAdapter(SearchPort):
    """Neural web search via Exa (metered; falls through the chain).

    Attributes:
        name: Adapter name.
    """

    name = "exa"

    def __init__(
        self,
        api_key: str,
        *,
        ledger: MonthlyQuotaLedger | None = None,
        timeout: float = 20.0,
        limit: int = EXA_MONTHLY_LIMIT,
    ) -> None:
        """Create an ExaAdapter.

        Args:
            api_key: Exa API key.
            ledger: Quota ledger; a monthly one is built when omitted.
            timeout: Request timeout in seconds.
            limit: Monthly request cap used when building the default ledger.
        """
        self.api_key = api_key
        self._timeout = timeout
        self.ledger = ledger or MonthlyQuotaLedger("exa", limit=limit)

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client with the API-key header.
        """
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        return httpx.AsyncClient(timeout=self._timeout, headers=headers)

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an Exa result to a record.

        Args:
            item: Exa result object.

        Returns:
            SourceRecord: Resolved source record.
        """
        url = item.get("url") or ""
        highlights = item.get("highlights") or []
        abstract = highlights[0] if highlights else item.get("text")
        published = item.get("publishedDate") or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        author = item.get("author")
        return SourceRecord(
            id=f"exa:{url}",
            doi=None,
            title=item.get("title") or "",
            source=SearchSource.EXA,
            authors=[author] if author else [],
            year=year,
            abstract=abstract or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Web search via Exa.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in Exa relevance order.

        Raises:
            QuotaExhausted: When the free quota is spent.
            SearchError: If the request fails.
        """
        await self.ledger.check()
        payload = {
            "query": query,
            "numResults": max(1, min(limit, 100)),
            "type": "instant",
            "contents": {"highlights": True},
        }
        try:
            async with self._client() as client:
                resp = await client.post(_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            raise SearchError(f"Exa search failed for {query!r}: {exc}") from exc
        if resp.status_code in _QUOTA_STATUS:
            await self.ledger.record(exhausted=True)
            raise QuotaExhausted(f"Exa quota exhausted (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise SearchError(f"Exa search failed for {query!r}: HTTP {resp.status_code}")
        items = resp.json().get("results", [])
        await self.ledger.record()
        return [self._to_record(item) for item in items[:limit]]

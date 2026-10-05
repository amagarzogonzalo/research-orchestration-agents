"""Wikipedia adapter — general-knowledge search over MediaWiki (keyless).

``action=query&list=search`` returns page titles plus HTML snippets, which are
stripped to plain text for abstracts. Keyless with a compliant User-Agent;
this is the always-on general-web fallback that needs no credentials.

Live probe 2026-10-05: ``srsearch=retrieval%20augmented%20generation``
HTTP 200 with ``query.search[].pageid/title/snippet``.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "knowledge-engine/0.1 (research library; contact: via repo)"
_TAG = re.compile(r"<[^>]+>")


class WikipediaAdapter(SearchPort):
    """General-knowledge search over Wikipedia (free, keyless).

    Attributes:
        name: Adapter name.
    """

    name = "wikipedia"

    def __init__(self, *, timeout: float = 15.0) -> None:
        """Create a WikipediaAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.
        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            item: MediaWiki search item.

        Returns:
            SourceRecord: Resolved source record.
        """
        abstract = _TAG.sub("", item.get("snippet") or "").strip()
        return SourceRecord(
            id=f"wikipedia:{item.get('pageid', '')}",
            doi=None,
            title=item.get("title") or "",
            source=SearchSource.WIKIPEDIA,
            authors=[],
            year=None,
            abstract=abstract or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Keyword search over Wikipedia articles.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in MediaWiki relevance order.

        Raises:
            SearchError: If the API call fails.
        """
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(limit, 50),
            "format": "json",
        }
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params=params)
                resp.raise_for_status()
                items = resp.json().get("query", {}).get("search", [])
                return [self._to_record(item) for item in items[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"Wikipedia search failed for {query!r}: {exc}") from exc

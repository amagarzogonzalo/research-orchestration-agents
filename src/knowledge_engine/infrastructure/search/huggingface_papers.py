"""HuggingFace Papers adapter — community-ranked ML papers (Papers With Code successor).

The Papers With Code API was retired (its endpoints now redirect to
HuggingFace), so this adapter targets its verified successor:
``GET https://huggingface.co/api/papers/search?q=<query>``. It returns papers
with community upvotes, which this adapter uses to rank results best-first —
the "papers with code, used strongly" signal: community-validated relevance
rather than pure keyword match.

Keyless, no account needed. The API exposes no server-side limit parameter, so
results are sliced client-side to ``limit``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://huggingface.co/api/papers/search"


class HuggingFacePapersAdapter(SearchPort):
    """Community-ranked paper search over HuggingFace Papers (free, keyless).

    Attributes:
        name: Attribute description.
    """

    name = "huggingface_papers"

    def __init__(self, *, timeout: float = 15.0) -> None:
        """Create a HuggingFacePapersAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
        self._timeout = timeout

    @staticmethod
    def _to_record(item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            item: Item.

        Returns:
            SourceRecord: Resolved source record.


        """
        paper = item.get("paper") or {}
        arxiv_id = str(paper.get("id") or "")
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        year: int | None = None
        published = paper.get("publishedAt") or item.get("publishedAt") or ""
        if len(published) >= 4 and published[:4].isdigit():
            year = int(published[:4])
        return SourceRecord(
            id=f"huggingface_papers:{arxiv_id}",
            doi=None,
            title=paper.get("title") or "",
            source=SearchSource.HUGGINGFACE_PAPERS,
            authors=authors,
            year=year,
            abstract=paper.get("summary"),
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search community-ranked papers, best (most upvoted) first.

        Args:
            query: Search terms.
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Records sorted by community upvotes, descending.

        Raises:
            SearchError: If the HuggingFace Papers API call fails.
        """
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params={"q": query})
                resp.raise_for_status()
                items = resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"HuggingFace Papers search failed for {query!r}: {exc}") from exc
        if not isinstance(items, list):
            raise SearchError(f"HuggingFace Papers returned unexpected payload for {query!r}")
        ranked = sorted(items, key=lambda it: int((it.get("paper") or {}).get("upvotes") or 0), reverse=True)
        return [self._to_record(item) for item in ranked[:limit]]

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.


        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

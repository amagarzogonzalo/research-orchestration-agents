"""OSF Preprints adapter — multidisciplinary preprints (keyless JSON:API).

The Open Science Framework aggregates SocArXiv, PsyArXiv, engrXiv and other
preprint servers behind one free, keyless endpoint. It fills the social-science
/ engineering gap left by arXiv + bioRxiv/medRxiv.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_BASE = "https://api.osf.io/v2/preprints/"

_STOPWORDS = frozenset(
    (
        "how what when where which that with from into over under after before "
        "and the for are was were has have had this these those its their there "
        "then than such can will just"
    ).split()
)


def _keywords(query: str) -> list[str]:
    """Split a query into significant single words for title fallback.

    Args:
        query: Scholarly query string.

    Returns:
        list[str]: Significant words, longest first.
    """
    words = [w.strip("?!:;.,()\"'").lower() for w in query.split()]
    seen: list[str] = []
    for word in words:
        if len(word) > 4 and word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return sorted(seen, key=len, reverse=True)


class OSFPreprintsAdapter(SearchPort):
    """Multidisciplinary preprints via the OSF API (keyless, polite User-Agent).

    Attributes:
        name: Adapter name.
    """

    name = "osf_preprints"

    def __init__(self, *, timeout: float = 20.0) -> None:
        """Create a OSFPreprintsAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
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
            item: JSON:API preprint object.

        Returns:
            SourceRecord: Resolved source record.
        """
        attrs = item.get("attributes") or {}
        doi = (attrs.get("doi") or "").strip() or None
        published = str(attrs.get("date_published") or "")
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
        return SourceRecord(
            id=f"osf:{item.get('id', '')}",
            doi=doi,
            title=attrs.get("title") or "",
            source=SearchSource.OSF_PREPRINTS,
            authors=[],
            year=year,
            abstract=attrs.get("description"),
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search OSF preprints by title.

        The title filter is phrase-exact, so a full natural-language query
        usually matches nothing: fall back to significant single words and
        merge, deduped by id.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved sources.

        Raises:
            SearchError: When the operation fails.
        """
        try:
            async with self._client() as client:
                records = await self._search_filter(client, query, limit)
                if records:
                    return records
                seen: dict[str, SourceRecord] = {}
                for word in _keywords(query)[:4]:
                    for rec in await self._search_filter(client, word, limit):
                        seen.setdefault(rec.id, rec)
                    if len(seen) >= limit:
                        break
                return list(seen.values())[:limit]
        except httpx.HTTPError as exc:
            raise SearchError(f"OSF search failed for {query!r}: {exc}") from exc

    async def _search_filter(self, client: httpx.AsyncClient, title: str, limit: int) -> list[SourceRecord]:
        """Run one title-filter query.

        Args:
            client: HTTP client.
            title: Title filter text.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved sources.
        """
        resp = await client.get(_BASE, params={"filter[title]": title, "page[size]": min(limit, 20)})
        resp.raise_for_status()
        return [self._to_record(item) for item in resp.json().get("data", [])[:limit]]

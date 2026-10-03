"""arXiv — free preprint access (physics, math, CS)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

_BASE = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom", "arx": "http://arxiv.org/schemas/atom"}


class ArxivAdapter(SearchPort):
    """ArxivAdapter.

    Attributes:
        name: Attribute description."""

    name = "arxiv"

    def __init__(self, *, timeout: float = 20.0) -> None:
        """Create a ArxivAdapter.

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
    def _to_record(entry: ET.Element) -> SourceRecord:
        """Convert an item to a record.

        Args:
            entry: Log entry.

        Returns:
            SourceRecord: Resolved source record.


        """

        def _tag(name: str) -> ET.Element | None:
            """Tag.

            Args:
                name: Object name.

            Returns:
                ET.Element | None: Element or None.

            """
            return entry.find(name, _NS)

        # NOTE: never test an Element for truthiness — childless elements are
        # falsy even when present, which silently blanked every leaf field.
        title_el = _tag("atom:title")
        title = re.sub(r"\s+", " ", (title_el.text or "").strip()) if title_el is not None else ""
        authors = []
        for author in entry.findall("atom:author", _NS):
            name = author.findtext("atom:name", namespaces=_NS)
            if name:
                authors.append(name.strip())
        summary_el = _tag("atom:summary")
        summary = re.sub(r"\s+", " ", (summary_el.text or "").strip()) if summary_el is not None else ""
        id_el = _tag("atom:id")
        id_link = id_el.text if id_el is not None and id_el.text else ""
        arxiv_id = id_link.rsplit("/abs/", 1)[-1] if id_link else ""
        published_el = _tag("atom:published")
        published = (published_el.text if published_el is not None and published_el.text else "")[:4]
        year = int(published) if published.isdigit() else None
        pdf_url = None
        for link in entry.findall("atom:link", _NS):
            if link.get("rel") == "related" and link.get("title") == "pdf":
                pdf_url = link.get("href")
        return SourceRecord(
            id=f"arxiv:{arxiv_id}",
            doi=None,
            title=title,
            source=SearchSource.ARXIV,
            authors=authors,
            year=year,
            abstract=summary,
            pdf_url=pdf_url,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

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
        params = {"search_query": f"all:{query}", "max_results": limit}
        try:
            async with self._client() as client:
                resp = await client.get(_BASE, params=params)
                if resp.status_code == 429:
                    # export.arxiv.org asks clients to back off (~3s between
                    # requests); one polite retry beats dropping the source.
                    await asyncio.sleep(5)
                    resp = await client.get(_BASE, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"arXiv search failed for {query!r}: {exc}") from exc
        root = ET.fromstring(resp.text)
        return [self._to_record(entry) for entry in root.findall("atom:entry", _NS)]

    # arXiv has no DOI fetch; FetchPort left unimplemented intentionally.
    async def fetch(self, doi: str):  # pragma: no cover - not applicable
        """Fetch a record.

        Args:
            doi: Digital object identifier.

        Raises:
            NotImplementedError: When the operation fails.
        """
        raise NotImplementedError("arXiv is not DOI-indexed; use the ID lookup variant")

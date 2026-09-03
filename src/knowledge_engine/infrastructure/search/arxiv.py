"""arXiv — free preprint access (physics, math, CS)."""

from __future__ import annotations

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
    name = "arxiv"

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    @staticmethod
    def _to_record(entry: ET.Element) -> SourceRecord:
        def _tag(name: str) -> ET.Element | None:
            return entry.find(name, _NS)

        title = re.sub(r"\s+", " ", (_tag("atom:title").text or "").strip()) if _tag("atom:title") else ""
        authors = []
        for author in entry.findall("atom:author", _NS):
            name = author.findtext("atom:name", namespaces=_NS)
            if name:
                authors.append(name.strip())
        summary = re.sub(r"\s+", " ", (_tag("atom:summary").text or "").strip()) if _tag("atom:summary") else ""
        id_link = _tag("atom:id").text if _tag("atom:id") else ""
        arxiv_id = id_link.rsplit("/abs/", 1)[-1] if id_link else ""
        published = (_tag("atom:published").text if _tag("atom:published") else "")[:4]
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
        params = {"search_query": f"all:{query}", "max_results": limit}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"}) as client:
                resp = await client.get(_BASE, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"arXiv search failed for {query!r}: {exc}") from exc
        root = ET.fromstring(resp.text)
        return [self._to_record(entry) for entry in root.findall("atom:entry", _NS)]

    # arXiv has no DOI fetch; FetchPort left unimplemented intentionally.
    async def fetch(self, doi: str):  # pragma: no cover - not applicable
        raise NotImplementedError("arXiv is not DOI-indexed; use the ID lookup variant")

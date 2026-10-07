"""PubMed / NCBI E-utilities adapter — biomedical backbone (33M+ records).

Two-step lookup: ``esearch.fcgi?db=pubmed`` resolves a query to PMIDs, then a
single batched ``esummary.fcgi`` call returns titles, authors, dates and DOIs.
Full Boolean/MeSH query syntax is passed through untouched.

Keyless use is limited to 3 requests/second, so every HTTP call goes through a
client-side minimum-interval throttle (``min_interval``, default 0.35s).
``fetch`` resolves a DOI via a ``[DOI]``-tagged esearch, keeping the DOI-first
contract the demux relies on.

Live probe 2026-10-01: ``esearch.fcgi?db=pubmed&term=attention+transformer``
HTTP 200, JSON ``esearchresult`` with ``count`` + ``idlist``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# esummary carries no abstracts; one batched efetch covers the top records.
_EFETCH_ABSTRACT_LIMIT = 5


def _parse_abstracts(xml_text: str) -> dict[str, str]:
    """Parse PMID -> abstract text from an efetch XML payload.

    Args:
        xml_text: efetch XML response.

    Returns:
        dict[str, str]: PMID to abstract text.
    """
    import xml.etree.ElementTree as ET

    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for article in root.iter("PubmedArticle"):
        pmid_el = article.find("./MedlineCitation/PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        parts = []
        for node in article.iter("AbstractText"):
            label = node.get("Label")
            text = "".join(node.itertext()).strip()
            if text:
                parts.append(f"{label}: {text}" if label else text)
        if parts:
            out[pmid_el.text.strip()] = "\n".join(parts)
    return out


class PubMedAdapter(SearchPort, FetchPort):
    """Biomedical search over PubMed E-utilities (free, keyless, 3 req/s).

    Attributes:
        name: Attribute description.
    """

    name = "pubmed"

    def __init__(self, *, timeout: float = 15.0, min_interval: float = 0.35, tool: str = "knowledge-engine") -> None:
        """Create a PubMedAdapter.

        Args:
            timeout: Request timeout in seconds.
            min_interval: Minimum interval in seconds.
            tool: Tool specification.

        """
        self._timeout = timeout
        self._min_interval = min_interval
        self._tool = tool
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.


        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": f"{self._tool}/pubmed"})

    async def _throttled_get(self, client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
        """Space keyless E-utility calls at least ``min_interval`` apart.

        Args:
            client: Async HTTP client.
            url: Target URL.
            params: Query parameters.

        Returns:
            httpx.Response: HTTP response.

        """
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(url, params=params)
            self._last_call = time.monotonic()
            return resp

    @staticmethod
    def _to_record(pmid: str, item: dict) -> SourceRecord:
        """Convert an item to a record.

        Args:
            pmid: PubMed identifier.
            item: Item.

        Returns:
            SourceRecord: Resolved source record.


        """
        authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
        doi: str | None = None
        for ident in item.get("articleids", []):
            if ident.get("idtype") == "doi" and ident.get("value"):
                doi = str(ident["value"])
                break
        pubdate = str(item.get("pubdate") or item.get("epubdate") or "")
        year = int(pubdate[:4]) if len(pubdate) >= 4 and pubdate[:4].isdigit() else None
        return SourceRecord(
            id=f"pubmed:{pmid}",
            doi=doi,
            title=item.get("title") or "",
            source=SearchSource.PUBMED,
            authors=authors,
            year=year,
            abstract=None,
            pdf_url=None,
            citation_count=0,
            venue=item.get("source") or None,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def _summaries(self, client: httpx.AsyncClient, pmids: list[str]) -> list[SourceRecord]:
        """One batched esummary call for up to ~200 PMIDs.

        Args:
            client: Async HTTP client.
            pmids: PubMed identifiers.

        Returns:
            list[SourceRecord]: Resolved sources.

        """
        if not pmids:
            return []
        resp = await self._throttled_get(client, _ESUMMARY, {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"})
        resp.raise_for_status()
        payload = resp.json().get("result", {})
        records = []
        for pmid in pmids:
            item = payload.get(str(pmid))
            if isinstance(item, dict):
                records.append(self._to_record(str(pmid), item))
        return records

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search PubMed (Boolean/MeSH syntax supported), then summarize hits.

        Args:
            query: Search terms (passed through to E-utilities verbatim).
            limit: Max records to return.

        Returns:
            list[SourceRecord]: Records in PubMed relevance order.

        Raises:
            SearchError: If the E-utilities calls fail.
        """
        try:
            async with self._client() as client:
                resp = await self._throttled_get(
                    client, _ESEARCH, {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit}
                )
                resp.raise_for_status()
                idlist = [str(i) for i in resp.json().get("esearchresult", {}).get("idlist", [])]
                records = await self._summaries(client, idlist[:limit])
                await self._fill_abstracts(client, records)
                return records
        except httpx.HTTPError as exc:
            raise SearchError(f"PubMed search failed for {query!r}: {exc}") from exc

    async def _fill_abstracts(self, client: httpx.AsyncClient, records: list[SourceRecord]) -> None:
        """Fill abstracts for the top records via one batched efetch call.

        Best-effort: failures leave abstracts empty rather than failing search.

        Args:
            client: HTTP client.
            records: Records to enrich in place.
        """
        targets = [r for r in records[:_EFETCH_ABSTRACT_LIMIT] if not r.abstract]
        if not targets:
            return
        try:
            pmids = [r.id.replace("pubmed:", "") for r in targets]
            resp = await self._throttled_get(
                client, _EFETCH, {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
            )
            resp.raise_for_status()
            for pmid, text in _parse_abstracts(resp.text).items():
                for record in targets:
                    if record.id == f"pubmed:{pmid}" and text:
                        record.abstract = text
        except Exception as exc:  # noqa: BLE001 - abstracts are enrichment, never fatal
            logger.warning("pubmed efetch abstracts failed: %s", exc)

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Resolve a DOI to a PubMed record via a ``[DOI]``-tagged lookup.

        Args:
            doi: DOI to resolve (bare, without URL prefix).

        Returns:
            SourceRecord | None: The resolved record, or None when PubMed does not index it.

        Raises:
            SearchError: If the E-utilities calls fail.
        """
        try:
            async with self._client() as client:
                resp = await self._throttled_get(
                    client, _ESEARCH, {"db": "pubmed", "term": f"{doi}[DOI]", "retmode": "json", "retmax": 1}
                )
                resp.raise_for_status()
                idlist = resp.json().get("esearchresult", {}).get("idlist", [])
                if not idlist:
                    return None
                records = await self._summaries(client, [str(idlist[0])])
                return records[0] if records else None
        except httpx.HTTPError as exc:
            raise SearchError(f"PubMed fetch failed for {doi}: {exc}") from exc

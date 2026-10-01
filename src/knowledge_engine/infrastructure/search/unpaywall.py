"""Unpaywall adapter — legal open-access resolution by DOI (100K/day free).

Unpaywall has no keyword-search endpoint, so this adapter implements
:mod:`FetchPort` (DOI → OA status + best legal PDF) and a documented no-op
``search`` returning ``[]`` so federated fan-out stays quiet. It feeds legal
OA PDFs to the scrape tier.

A real contact email is mandatory: the API rejects placeholder addresses
(live probe 2026-10-01: ``?email=test@example.com`` → HTTP 422). The address
comes from ``UNPAYWALL_EMAIL``; without a usable one the adapter is never
wired (see ``_default_search``), and ``fetch`` raises
:mod:`ConfigurationError` instead of burning a doomed request.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import ConfigurationError, SearchError
from knowledge_engine.core.ports import FetchPort, SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_PLACEHOLDER_MARKERS = ("example.com", "placeholder", "your-email", "change_me", "test")


def is_usable_email(email: str | None) -> bool:
    """True when ``email`` is a real contact address, not a placeholder.

    Args:
        email: Candidate address (usually from ``UNPAYWALL_EMAIL``).

    Returns:
        bool: False for empty, malformed, or placeholder addresses.
    """
    if not email or "@" not in email:
        return False
    lowered = email.strip().lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class UnpaywallAdapter(SearchPort, FetchPort):
    """OA resolution by DOI (free tier, email required, no key)."""

    name = "unpaywall"

    def __init__(self, email: str = "", *, timeout: float = 15.0) -> None:
        self.email = email.strip()
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": "knowledge-engine"})

    @staticmethod
    def _to_record(item: dict, doi: str) -> SourceRecord:
        best = item.get("best_oa_location") or {}
        authors = [
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in item.get("z_authors", [])
            if a.get("family") or a.get("given")
        ]
        published = str(item.get("published_date") or "")
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
        return SourceRecord(
            id=f"unpaywall:{doi}",
            doi=item.get("doi") or doi,
            title=item.get("title") or "",
            source=SearchSource.UNPAYWALL,
            authors=authors,
            year=year,
            abstract=None,
            pdf_url=best.get("url_for_pdf"),
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Unpaywall offers no keyword search; always returns ``[]``.

        Present only so federated fan-out stays quiet (Demux isolates per-source
        failures, but silence beats a logged warning every run).
        """
        return []

    async def fetch(self, doi: str) -> SourceRecord | None:
        """Resolve a DOI to its OA status and best legal PDF.

        Args:
            doi: DOI to resolve (bare, without URL prefix).

        Returns:
            SourceRecord | None: The record (``pdf_url`` set when OA), or None when not OA/indexed.

        Raises:
            ConfigurationError: If no usable contact email was configured.
            SearchError: If the API call fails.
        """
        if not is_usable_email(self.email):
            raise ConfigurationError("Unpaywall needs a real UNPAYWALL_EMAIL; refusing a doomed request")
        try:
            async with self._client() as client:
                resp = await client.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": self.email})
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._to_record(resp.json(), doi)
        except httpx.HTTPError as exc:
            raise SearchError(f"Unpaywall fetch failed for {doi}: {exc}") from exc

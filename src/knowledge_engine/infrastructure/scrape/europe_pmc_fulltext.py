"""Europe PMC full-text adapter — OA XML instead of scraped article HTML.

Article pages (``europepmc.org/article/...``) 403 browserless fetch from this
egress, so scraping them always fails. The keyless REST ``fullTextXML``
endpoint serves the same content as JATS XML for the open-access subset
(``.../rest/PMC{digits}/fullTextXML`` — the ID form matters: a ``PMC/PMC…``
slash form 404s). Non-OA records have no XML (404) and fall through to the
rest of the scrape chain via :class:`ScrapeError`. MED records have no direct
full-text route and are skipped (the browser tier covers them).

Verified live 2026-10-07: ``.../rest/PMC3741566/fullTextXML`` HTTP 200 with
43k chars of JATS ``<body>`` paragraphs.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.common import fallback_title, is_transient_http
from knowledge_engine.schemas.source import DocumentBody, FetchMethod

logger = logging.getLogger(__name__)

_PMC_RE = re.compile(r"europepmc\.org/article/PMC/(?:PMC)?(\d+)", re.IGNORECASE)
_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC{digits}/fullTextXML"
_USER_AGENT = "knowledge-engine/0.1 (https://github.com/amagarzogonzalo/research-orchestration-agents)"


class EuropePmcFullTextAdapter(ScrapePort):
    """Full text for Europe PMC records via the REST fullTextXML endpoint.

    Attributes:
        name: Adapter name.
    """

    name = "europe_pmc_fulltext"

    def __init__(self, *, timeout: float = 20.0) -> None:
        """Create a EuropePmcFullTextAdapter.

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
    def _rest_url(url: str) -> str | None:
        """Map a PMC article page URL to its fullTextXML endpoint.

        Args:
            url: Candidate Europe PMC article URL.

        Returns:
            str | None: REST URL, or None for non-PMC URLs (MED skipped).
        """
        match = _PMC_RE.search(url or "")
        if not match:
            return None
        return _REST.format(digits=match.group(1))

    @staticmethod
    def _body_text(xml_text: str) -> tuple[str, str | None]:
        """Extract the article title and body paragraphs from JATS XML.

        Args:
            xml_text: Raw fullTextXML document.

        Returns:
            tuple[str, str | None]: Body text and article title if present.

        Raises:
            ScrapeError: If the XML cannot be parsed or holds no text.
        """
        try:
            root = ET.fromstring(xml_text)  # noqa: S314 - trusted API response, parsed not executed
        except ET.ParseError as exc:
            raise ScrapeError(f"Europe PMC returned unparseable XML: {exc}") from exc
        for element in root.iter():
            if "}" in element.tag:
                element.tag = element.tag.split("}", 1)[1]
        title_element = root.find(".//article-title")
        title = " ".join("".join(title_element.itertext()).split()) if title_element is not None else None
        body = root.find(".//body")
        scope = body if body is not None else root
        paragraphs = scope.findall(".//p") or [scope]
        texts = [" ".join("".join(p.itertext()).split()) for p in paragraphs]
        text = "\n\n".join(t for t in texts if t)
        if not text:
            raise ScrapeError("Europe PMC full text holds no usable paragraphs")
        return text, title

    async def to_markdown(self, url: str) -> DocumentBody:
        """Fetch the OA full text for a Europe PMC article page.

        Args:
            url: Europe PMC article page URL.

        Returns:
            DocumentBody: Full-text body with the original page URL.

        Raises:
            ScrapeError: When the URL is not a PMC article page, the record
                is not open access, or the XML holds no usable text.
        """
        rest_url = self._rest_url(url)
        if rest_url is None:
            raise ScrapeError(f"not a Europe PMC PMC article URL: {url}")
        try:
            async with self._client() as client:
                resp = await client.get(rest_url)
                if resp.status_code == 404:
                    raise ScrapeError(f"Europe PMC has no OA full text for {url}")
                resp.raise_for_status()
                text, title = self._body_text(resp.text)
        except httpx.HTTPError as exc:
            raise ScrapeError(
                f"Europe PMC full-text fetch failed for {url}: {exc}",
                transient=is_transient_http(exc),
            ) from exc
        return DocumentBody(
            url=url,
            title=title or fallback_title(url),
            markdown=text,
            char_count=len(text),
            crawl_method=FetchMethod.API,
            captured_at=datetime.now(UTC).isoformat(),
        )

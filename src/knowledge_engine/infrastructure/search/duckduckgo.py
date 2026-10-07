"""DuckDuckGo adapter — general-web search without signup or keys.

The keyless ``html.duckduckgo.com/html`` endpoint returns real web results
without a key or signup. Result links are redirect-wrapped
(``/l/?uddg=<target>``) and unwrapped here. It is the first provider in the
general-web fallback chain (Tavily/Exa follow when their keys are set).
Parsing is stdlib-only so this tier adds no dependency. Polite single-shot
POST per query; the demux isolates failures like any other source.

Live probe 2026-10-07: ``q=70B LLM local inference VRAM quantization``
HTTP 200 with 10 ``result__a`` links + ``result__snippet`` texts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

_ENDPOINT = "https://html.duckduckgo.com/html/"
_USER_AGENT = "knowledge-engine/0.1 (https://github.com/amagarzogonzalo/research-orchestration-agents)"


def _unwrap(href: str) -> str:
    """Resolve DDG redirect wrappers to the real target URL.

    Args:
        href: Raw link href from the results page.

    Returns:
        str: Target URL, or the href itself when already direct.
    """
    if "duckduckgo.com/l/" not in href:
        return href
    query = parse_qs(urlparse(href).query)
    target = (query.get("uddg") or [""])[0]
    return unquote(target) or href


class _ResultsParser(HTMLParser):
    """Collect (title, url, snippet) triples from DDG html results."""

    def __init__(self) -> None:
        """Create an empty results parser."""
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track result links and snippet anchors.

        Args:
            tag: HTML tag name.
            attrs: Tag attributes.
        """
        if tag != "a":
            return
        classes = dict(attrs).get("class", "") or ""
        href = dict(attrs).get("href", "") or ""
        if "result__a" in classes:
            self._current = {"title": "", "url": _unwrap(href), "snippet": ""}
            self.results.append(self._current)
            self._capture = "title"
        elif "result__snippet" in classes:
            self._capture = "snippet"

    def handle_data(self, data: str) -> None:
        """Append text to the field currently being captured.

        Args:
            data: Raw text content.
        """
        if self._capture == "title" and self._current is not None:
            self._current["title"] += data
        elif self._capture == "snippet" and self.results:
            self.results[-1]["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        """Stop capturing when an anchor closes.

        Args:
            tag: HTML tag name.
        """
        if tag == "a":
            self._capture = None


class DuckDuckGoAdapter(SearchPort):
    """General-web search via DuckDuckGo html (free, keyless, no signup).

    Attributes:
        name: Adapter name.
    """

    name = "duckduckgo"

    def __init__(self, *, timeout: float = 20.0) -> None:
        """Create a DuckDuckGoAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        """Return the client.

        Returns:
            httpx.AsyncClient: HTTP client.
        """
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)

    @staticmethod
    def _to_record(item: dict[str, str]) -> SourceRecord:
        """Convert a parsed result to a record.

        Args:
            item: Parsed title/url/snippet mapping.

        Returns:
            SourceRecord: Resolved source record.
        """
        return SourceRecord(
            id=f"duckduckgo:{item['url']}",
            doi=None,
            title=item["title"].strip(),
            source=SearchSource.DUCKDUCKGO,
            authors=[],
            year=None,
            abstract=item["snippet"].strip() or None,
            pdf_url=None,
            citation_count=0,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Web search via DuckDuckGo html.

        Args:
            query: Search terms.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Records in DDG relevance order.

        Raises:
            SearchError: If the fetch or parse fails.
        """
        try:
            async with self._client() as client:
                resp = await client.post(_ENDPOINT, data={"q": query})
                resp.raise_for_status()
                parser = _ResultsParser()
                parser.feed(resp.text)
                items = [item for item in parser.results if item["url"] and item["title"].strip()]
                return [self._to_record(item) for item in items[:limit]]
        except httpx.HTTPError as exc:
            raise SearchError(f"DuckDuckGo search failed for {query!r}: {exc}") from exc

"""Trafilatura adapter — browserless, ultra-fast text extraction.
Fast fallback when a full browser render is unnecessary (static HTML)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.schemas.source import DocumentBody, FetchMethod


class TrafilaturaAdapter(ScrapePort):
    def __init__(self, *, timeout: float = 15.0) -> None:
        """Create a TrafilaturaAdapter.

        Args:
            timeout: Request timeout in seconds.
        """
        self.timeout = timeout

    async def to_markdown(self, url: str) -> DocumentBody:
        """Convert a URL to Markdown.

        Args:
            url: Target URL.

        Returns:
            DocumentBody: Fetched Markdown body.

        Raises:
            ScrapeError: When the operation fails.

        """
        try:
            import trafilatura  # lazy import keeps heavy dep optional

            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "knowledge-engine"}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            html = resp.text
        except Exception as exc:  # noqa: BLE001 - network + trafilatura import
            raise ScrapeError(f"Trafilatura fetch failed for {url}: {exc}") from exc

        try:
            markdown = (
                trafilatura.extract(html, url=url, include_links=False, include_images=False, output_format="markdown")
                or ""
            )
        except Exception as exc:  # noqa: BLE001
            raise ScrapeError(f"Trafilatura extraction failed for {url}: {exc}") from exc
        return DocumentBody(
            url=url,
            title=_fallback_title(url),
            markdown=markdown,
            char_count=len(markdown),
            crawl_method=FetchMethod.TRAFILATURA,
            captured_at=datetime.now(UTC).isoformat(),
        )


def _fallback_title(url: str) -> str:
    """Fallback title.

    Args:
        url: Target URL.

    Returns:
        str: Result text.
    """
    return url.rstrip("/").rsplit("/", 1)[-1] or url

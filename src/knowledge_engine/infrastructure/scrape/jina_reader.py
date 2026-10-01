"""Jina Reader adapter — server-side URL-to-Markdown (keyless tier).

``GET https://r.jina.ai/<url>`` returns an envelope (``Title:`` / ``URL
Source:`` / ``Markdown Content:``) with LLM-ready Markdown. Its value is
server-side fetching: pages that block direct local fetch (bot protection,
JS shells) often succeed here. Keyless use is rate-limited (~20 RPM per the
official table); an optional ``JINA_API_KEY`` raises the ceiling. No new
dependency — plain HTTPS via httpx.

Position in the chain is last resort (after the local extractors), because it
is remote and metered while trafilatura/crawl4ai run free on this machine.

Live probe 2026-10-01: ``r.jina.ai/https://example.com`` HTTP 200 with the
documented envelope and Markdown body.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import httpx

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.schemas.source import DocumentBody, FetchMethod

logger = logging.getLogger(__name__)

_BASE = "https://r.jina.ai"
_MARKER = "Markdown Content:"


class JinaReaderAdapter(ScrapePort):
    """Remote reader fallback for pages local fetch cannot retrieve."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("JINA_API_KEY")
        self.timeout = timeout

    async def to_markdown(self, url: str) -> DocumentBody:
        """Fetch a URL through Jina Reader and return clean Markdown.

        Args:
            url: Page URL to render.

        Returns:
            DocumentBody: Extracted Markdown, marked with ``FetchMethod.JINA``.

        Raises:
            ScrapeError: If the Reader call fails or returns no content.
        """
        headers = {"User-Agent": "knowledge-engine"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
                resp = await client.get(f"{_BASE}/{url}")
                resp.raise_for_status()
                title, markdown = _parse(resp.text)
        except httpx.HTTPError as exc:
            raise ScrapeError(f"Jina Reader fetch failed for {url}: {exc}") from exc
        if not markdown.strip():
            raise ScrapeError(f"Jina Reader returned no content for {url}")
        return DocumentBody(
            url=url,
            title=title or url,
            markdown=markdown,
            char_count=len(markdown),
            crawl_method=FetchMethod.JINA,
            captured_at=datetime.now(UTC).isoformat(),
        )


def _parse(text: str) -> tuple[str, str]:
    """Split a Reader envelope into (title, markdown body)."""
    title = ""
    for line in text.splitlines():
        if line.startswith("Title:"):
            title = line.partition("Title:")[2].strip()
            break
    _, _, body = text.partition(_MARKER)
    return title, body.strip()

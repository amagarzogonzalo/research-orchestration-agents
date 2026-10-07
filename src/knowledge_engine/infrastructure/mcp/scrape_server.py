"""scrape_server — FastMCP server exposing URL→Markdown scraping as a tool.

Wraps an injected :class:`ScrapePort` (crawl4ai / Playwright / trafilatura chain)
so an agentic node can fetch LLM-ready Markdown over MCP.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from knowledge_engine.core.ports import ScrapePort


def build_scrape_server(scrape: ScrapePort, *, name: str = "web-scraper") -> FastMCP:
    """Construct an in-process FastMCP server exposing ``fetch_markdown``.

    Args:
        scrape: Scrape-to-markdown port.
        name: Object name.

    Returns:
        FastMCP: Configured server.

    """
    server = FastMCP(name)

    @server.tool
    async def fetch_markdown(url: str) -> dict[str, Any]:
        """Fetch a URL and return LLM-ready Markdown.

        Args:
            url: Target URL.

        Returns:
            dict[str, Any]: Result mapping.

        """
        body = await scrape.to_markdown(url)
        return body.model_dump(mode="json")

    return server


__all__ = ["build_scrape_server"]

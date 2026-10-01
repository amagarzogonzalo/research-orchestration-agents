"""search_server — FastMCP server exposing federated scholarly search as a tool.

Wraps an injected :class:`SearchPort` (the same adapter the deterministic
``ingest`` node uses) so an agentic node can query OpenAlex / Semantic Scholar
etc. over MCP. Returns JSON-safe records (raw data, not formatted text).
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from knowledge_engine.core.ports import SearchPort


def build_search_server(search: SearchPort, *, name: str = "scholarly-search") -> FastMCP:
    """Construct an in-process FastMCP server exposing ``search_papers``."""
    server = FastMCP(name)

    @server.tool
    async def search_papers(query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search scholarly literature across the federated demux (open metadata, biomedical, preprints, OA)."""
        records = await search.search(query, limit=limit)
        return [r.model_dump(mode="json") for r in records]

    return server


__all__ = ["build_search_server"]

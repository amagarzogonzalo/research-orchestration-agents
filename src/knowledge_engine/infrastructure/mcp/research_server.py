"""research_server — FastMCP server exposing the research engine as tools.

Lets outside agents (or our own MCP-routed branches) run research over MCP:
``research_quick`` for a single-pass answer, ``research_deep`` for the full
recursive workflow. Transports are injected callables (not the engine) so this
adapter family stays decoupled from the composition root. Returns JSON-safe
mappings (raw data, not formatted text).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP

ResearchCallable = Callable[[str], Awaitable[dict[str, Any]]]
"""Async research entrypoint: query in, JSON-safe result mapping out."""


def build_research_server(
    *,
    quick: ResearchCallable,
    deep: ResearchCallable | None = None,
    name: str = "research",
) -> FastMCP:
    """Construct an in-process FastMCP server exposing research tools.

    Args:
        quick: Single-pass research callable.
        deep: Recursive research callable (``research_deep`` omitted when None).
        name: Object name.

    Returns:
        FastMCP: Configured server.
    """
    server = FastMCP(name)

    @server.tool
    async def research_quick(query: str) -> dict[str, Any]:
        """Run single-pass scholarly research (search, synthesize, render).

        Args:
            query: Research question.

        Returns:
            dict[str, Any]: JSON-safe result mapping.
        """
        return await quick(query)

    if deep is not None:

        @server.tool
        async def research_deep(query: str) -> dict[str, Any]:
            """Run recursive deep research (intent, plan, parallel branches, judge, review).

            Args:
                query: Research question.

            Returns:
                dict[str, Any]: JSON-safe result mapping.
            """
            return await deep(query)

    return server


__all__ = ["ResearchCallable", "build_research_server"]

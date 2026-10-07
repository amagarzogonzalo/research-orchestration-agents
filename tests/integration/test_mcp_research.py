"""Integration tests: research MCP server exposes quick/deep tools (offline)."""

from __future__ import annotations

import json
import warnings
from typing import Any

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r"`langchain\.mcp` is in beta")
    from langchain.mcp import MCPAdapter

from knowledge_engine.infrastructure.mcp.research_server import build_research_server


async def _quick(query: str) -> dict[str, Any]:
    """Fake quick research callable.

    Args:
        query: Research question.

    Returns:
        dict[str, Any]: Result mapping.
    """
    return {"query": query, "mode": "quick", "notes": ["n1"]}


async def _deep(query: str) -> dict[str, Any]:
    """Fake deep research callable.

    Args:
        query: Research question.

    Returns:
        dict[str, Any]: Result mapping.
    """
    return {"query": query, "mode": "deep", "notes": ["n1", "n2"]}


async def test_research_server_exposes_both_tools() -> None:
    server = build_research_server(quick=_quick, deep=_deep)
    async with MCPAdapter(server) as adapter:
        tools = await adapter.list_tools()
    assert {t.name for t in tools} == {"research_quick", "research_deep"}


async def test_research_tools_roundtrip() -> None:
    server = build_research_server(quick=_quick, deep=_deep)
    async with MCPAdapter(server) as adapter:
        tools = {t.name: t for t in await adapter.list_tools()}

    def _mode(raw: object) -> str:
        if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
            raw = raw[0]["text"]
        resolved: Any = json.loads(raw) if isinstance(raw, str) else raw
        return str(resolved["mode"])

    assert _mode(await tools["research_quick"].ainvoke({"query": "q"})) == "quick"
    assert _mode(await tools["research_deep"].ainvoke({"query": "q"})) == "deep"


async def test_research_server_quick_only_omits_deep() -> None:
    server = build_research_server(quick=_quick)
    async with MCPAdapter(server) as adapter:
        tools = await adapter.list_tools()
    assert [t.name for t in tools] == ["research_quick"]

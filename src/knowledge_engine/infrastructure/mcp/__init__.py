"""MCP tool layer (ADR 0008 §6).

FastMCP servers wrap the existing search/scrape adapters; agent nodes load the
exposed tools via :class:`langchain.mcp.MCPAdapter`. Deterministic nodes keep
calling ports directly so unit tests never need a live MCP server or LLM.
"""

from __future__ import annotations

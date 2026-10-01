"""MCP toolkit — load FastMCP tools + build a create_agent graph (ADR 0008 §6).

* ``MCPAdapter`` (from ``langchain.mcp``) discovers tools on an in-process
  FastMCP server and adapts them to LangChain tools. In-process servers keep
  unit/integration tests offline (no subprocess, no socket).
* ``build_agent`` compiles a :func:`langchain.agents.create_agent` graph with
  those tools bound to a :class:`ProviderChatModel` (which bridges our
  zero-cost ``ProviderPort``/FallbackRouter into LangChain).
* Tool surface today: scholarly search, URL→markdown scrape, and full browser
  automation (``browser_act``) — all via the same loader.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP

# Suppress langchain's (official) "mcp is in beta" notice per import: the API is
# the documented SOTA surface and the warning adds noise to every test run.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r"`langchain\.mcp` is in beta")
    from langchain.agents import create_agent
    from langchain.mcp import MCPAdapter

from langchain_core.tools import BaseTool

from knowledge_engine.core.ports import BrowserPort, ScrapePort, SearchPort
from knowledge_engine.infrastructure.mcp.browser_server import build_browser_server
from knowledge_engine.infrastructure.mcp.model import ProviderChatModel
from knowledge_engine.infrastructure.mcp.scrape_server import build_scrape_server
from knowledge_engine.infrastructure.mcp.search_server import build_search_server

logger = logging.getLogger(__name__)


async def load_tools(
    search: SearchPort | None = None,
    scrape: ScrapePort | None = None,
    browser: BrowserPort | None = None,
) -> list[BaseTool]:
    """Discover MCP tools from the provided servers (single shared context).

    Args:
        search: Optional search port → ``search_papers`` tool.
        scrape: Optional scrape port → ``fetch_markdown`` tool.
        browser: Optional browser port → ``browser_act`` tool (full automation).

    Returns:
        list[BaseTool]: LangChain tools discovered from the enabled servers.
    """
    servers: list[FastMCP] = []
    if search is not None:
        servers.append(build_search_server(search))
    if scrape is not None:
        servers.append(build_scrape_server(scrape))
    if browser is not None:
        servers.append(build_browser_server(browser))
    if not servers:
        return []
    tools: list[BaseTool] = []
    for server in servers:
        async with MCPAdapter(server) as adapter:
            tools.extend(await adapter.list_tools())
    return tools


def build_agent(model: Any, tools: list[BaseTool], *, system_prompt: str | None = None) -> Any:
    """Compile a create_agent graph bound to ``tools`` with ``model`` (SOTA).

    Args:
        model: A LangChain ``BaseChatModel`` (e.g. our ``ProviderChatModel``).
        tools: Discovered LangChain tools.
        system_prompt: Optional system prompt for the agent.

    Returns:
        Any: A compiled ``create_agent`` graph.
    """
    return create_agent(model, tools, system_prompt=system_prompt or "")


def make_agent_builder(
    provider: Any,
    *,
    search: SearchPort | None = None,
    scrape: ScrapePort | None = None,
    browser: BrowserPort | None = None,
    system_prompt: str | None = None,
) -> Callable[[], Awaitable[Any]]:
    """Return an async callable that lazily builds a create_agent graph.

    Args:
        provider: A ``ProviderPort`` (FallbackRouter) backing the chat model.
        search: Optional search port for the tool surface.
        scrape: Optional scrape port for the tool surface.
        browser: Optional browser port for the tool surface.
        system_prompt: Optional system prompt.

    Returns:
        Callable[[], Awaitable[Any]]: An async callable yielding the compiled agent graph.
    """

    async def _build() -> Any:
        tools = await load_tools(search=search, scrape=scrape, browser=browser)
        return build_agent(ProviderChatModel(provider=provider), tools, system_prompt=system_prompt)

    return _build


__all__ = ["ProviderChatModel", "build_agent", "load_tools", "make_agent_builder"]

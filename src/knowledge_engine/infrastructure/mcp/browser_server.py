"""browser_server — FastMCP server exposing full browser automation as a tool.

Exposes ``browser_act(action, ...)`` so an agent can scrape web content,
submit forms, or test UIs over MCP — matching the SOTA pattern where the
action layer is an MCP tool and the deterministic render layer stays on
``ScrapePort``.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from knowledge_engine.core.ports import BrowserActionPort, BrowserPort


def build_browser_server(browser: BrowserPort | BrowserActionPort, *, name: str = "browser") -> FastMCP:
    """Construct an in-process FastMCP server exposing ``browser_act``.

    Args:
        browser: Any object implementing ``act(action, **kwargs)``.
        name: Server name used for tool namespace.

    Returns:
        FastMCP: A configured server whose ``browser_act`` tool calls the browser.
    """
    server = FastMCP(name)

    @server.tool
    async def browser_act(action: str, **kwargs: Any) -> dict[str, Any]:
        """Perform a browser action: navigate, click, fill, submit, extract_text,
        extract_markdown, screenshot, or assert_visible. Returns a JSON-safe result.

        Args:
            action: The browser action to perform.
            **kwargs: Action-specific arguments (url, selector, value, path...).

        Returns:
            dict[str, Any]: A JSON-safe ``BrowserActionResult``.

        Raises:
            RuntimeError: If the browser has no ``act`` method.
        """
        method = getattr(browser, "act", None)
        if method is None:
            raise RuntimeError(f"browser {type(browser).__name__} has no act()")
        result = await method(action, **kwargs)
        data = result.model_dump(mode="json")
        return dict(data)

    return server


__all__ = ["build_browser_server"]

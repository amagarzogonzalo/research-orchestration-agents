"""Browser automation schemas: typed action requests + results (shared cross-port API).

These models are the *contract* between agent tool calls and the browser
adapters. Everything is JSON-safe so an action can round-trip through MCP,
a file log, or a CLI without leaking a live page object.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrowserActionResult(BaseModel):
    """Outcome of a single browser action.

    Attributes:
        model_config: Attribute description.
        ok: Attribute description.
        action: Attribute description.
        url: Attribute description.
        text: Attribute description.
        markdown: Attribute description.
        screenshot_path: Attribute description.
        meta: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    action: str
    url: str = ""
    text: str | None = None
    markdown: str | None = None
    screenshot_path: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(
        cls,
        action: str,
        *,
        url: str = "",
        text: str | None = None,
        markdown: str | None = None,
        screenshot_path: str | None = None,
        **meta: Any,
    ) -> BrowserActionResult:
        """Build a success result with the shared fields populated.

        Args:
            action: Browser action name.
            url: Target URL.
            text: Encoded text.
            markdown: Markdown content.
            screenshot_path: Screenshot path.
            **meta: Extra metadata.

        Returns:
            BrowserActionResult: Action outcome.

        """
        return cls(
            ok=True,
            action=action,
            url=url,
            text=text,
            markdown=markdown,
            screenshot_path=screenshot_path,
            meta=meta,
        )

    @classmethod
    def failure(cls, action: str, reason: str, *, url: str = "") -> BrowserActionResult:
        """Build a failure result carrying the reason in meta.

        Args:
            action: Browser action name.
            reason: Failure reason.
            url: Target URL.

        Returns:
            BrowserActionResult: Action outcome.

        """
        return cls(ok=False, action=action, url=url, meta={"reason": reason})


__all__ = ["BrowserActionResult"]

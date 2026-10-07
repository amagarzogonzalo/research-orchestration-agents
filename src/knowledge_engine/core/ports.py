"""Ports: abstract contracts the core depends on (ADR 0001).

Adapters implement these; the core never imports a vendor SDK. Every port
exchanges only Pydantic types defined under :mod:`knowledge_engine.schemas`.
"""

from __future__ import annotations

import abc
from typing import Any

from knowledge_engine.schemas.browser import BrowserActionResult
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import ChatRequest, ChatResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord


class ProviderPort(abc.ABC):
    """LLM transport. Implemented by FallbackRouter over concrete adapters."""

    @abc.abstractmethod
    async def complete(self, req: ChatRequest) -> ChatResult:
        """Complete a chat request.

        Args:
            req: Chat request.

        Returns:
            ChatResult: Chat result.
        """
        ...

    @abc.abstractmethod
    async def extract(self, req: ExtractionRequest) -> object:
        """Structured extraction; returns an instance of req.schema_ref's model.

        Args:
            req: Extraction request.

        Returns:
            object: Instance of the requested schema model.
        """
        ...


class SearchPort(abc.ABC):
    """Federated scholarly search."""

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search scholarly sources.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved scholarly sources.
        """
        ...


class FetchPort(abc.ABC):
    """DOI/ID-first resolution (free OpenAlex path; preserves search quota)."""

    @abc.abstractmethod
    async def fetch(self, doi: str) -> SourceRecord | None:
        """Fetch a record by DOI.

        Args:
            doi: Digital object identifier.

        Returns:
            SourceRecord | None: Resolved record, or None when unknown.
        """
        ...


class ScrapePort(abc.ABC):
    """URL -> LLM-ready Markdown."""

    @abc.abstractmethod
    async def to_markdown(self, url: str) -> DocumentBody:
        """Fetch a URL as LLM-ready Markdown.

        Args:
            url: Target URL.

        Returns:
            DocumentBody: Markdown body.
        """
        ...


class BrowserPort(abc.ABC):
    """Full browser automation: render + interact with live pages."""

    @abc.abstractmethod
    async def navigate(self, url: str, *, wait_selector: str = "body") -> BrowserActionResult:
        """Navigate to a URL.

        Args:
            url: Target URL.
            wait_selector: Selector to wait for.

        Returns:
            BrowserActionResult: Navigation outcome.
        """
        ...

    @abc.abstractmethod
    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Click an element.

        Args:
            selector: CSS selector.
            timeout_ms: Wait timeout in milliseconds.

        Returns:
            BrowserActionResult: Click outcome.
        """
        ...

    @abc.abstractmethod
    async def fill(self, selector: str, value: str) -> BrowserActionResult:
        """Fill an input field.

        Args:
            selector: CSS selector.
            value: Value to fill.

        Returns:
            BrowserActionResult: Fill outcome.
        """
        ...

    @abc.abstractmethod
    async def submit(self, selector: str | None = None) -> BrowserActionResult:
        """Submit a form.

        Args:
            selector: Optional submit-button selector.

        Returns:
            BrowserActionResult: Submit outcome.
        """
        ...

    @abc.abstractmethod
    async def extract_text(self, selector: str = "body") -> BrowserActionResult:
        """Extract visible text.

        Args:
            selector: CSS selector scope.

        Returns:
            BrowserActionResult: Extraction outcome.
        """
        ...

    @abc.abstractmethod
    async def extract_markdown(self) -> BrowserActionResult:
        """Extract the page as Markdown.

        Returns:
            BrowserActionResult: Extraction outcome.
        """
        ...

    @abc.abstractmethod
    async def screenshot(self, path: str | None = None) -> BrowserActionResult:
        """Capture a screenshot.

        Args:
            path: Optional output path.

        Returns:
            BrowserActionResult: Screenshot outcome.
        """
        ...

    @abc.abstractmethod
    async def assert_visible(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Assert an element is visible.

        Args:
            selector: CSS selector.
            timeout_ms: Wait timeout in milliseconds.

        Returns:
            BrowserActionResult: Assertion outcome.
        """
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """Release browser resources."""
        ...


class BrowserActionPort(abc.ABC):
    """Serializable action surface for agent tools (MCP ``browser_act``)."""

    @abc.abstractmethod
    async def act(self, action: str, **kwargs: Any) -> BrowserActionResult:
        """Dispatch a named browser action.

        Args:
            action: Action name (navigate/click/fill/submit/...).
            **kwargs: Action-specific arguments.

        Returns:
            BrowserActionResult: Action outcome.
        """
        ...

"""Ports: abstract contracts the core depends on (ADR 0001).

Adapters implement these; the core never imports a vendor SDK. Every port
exchanges only Pydantic types defined under :mod:`knowledge_engine.schemas`.
"""

from __future__ import annotations

import abc

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


class BrokerPort(abc.ABC):
    """Event shell transport (Redis Streams in production)."""

    @abc.abstractmethod
    async def publish(self, stream: str, payload: dict) -> str:
        """Publish an event.

        Args:
            stream: Stream name.
            payload: Event payload.

        Returns:
            str: Event identifier.
        """
        ...

    @abc.abstractmethod
    async def consume_group(self, group: str, consumer: str, handler: object) -> None:
        """Consume a group stream.

        Args:
            group: Consumer group name.
            consumer: Consumer name.
            handler: Event handler callable.
        """
        ...

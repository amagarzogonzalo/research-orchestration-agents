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
    async def complete(self, req: ChatRequest) -> ChatResult: ...

    @abc.abstractmethod
    async def extract(self, req: ExtractionRequest) -> object:
        """Structured extraction; returns an instance of req.schema_ref's model."""
        ...


class SearchPort(abc.ABC):
    """Federated scholarly search."""

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]: ...


class FetchPort(abc.ABC):
    """DOI/ID-first resolution (free OpenAlex path; preserves search quota)."""

    @abc.abstractmethod
    async def fetch(self, doi: str) -> SourceRecord | None: ...


class ScrapePort(abc.ABC):
    """URL -> LLM-ready Markdown."""

    @abc.abstractmethod
    async def to_markdown(self, url: str) -> DocumentBody: ...


class BrokerPort(abc.ABC):
    """Event shell transport (Redis Streams in production)."""

    @abc.abstractmethod
    async def publish(self, stream: str, payload: dict) -> str: ...

    @abc.abstractmethod
    async def consume_group(self, group: str, consumer: str, handler) -> None: ...
"""Domain exceptions shared across ports and adapters."""

from __future__ import annotations


class KnowledgeEngineError(Exception):
    """Base class for all engine errors."""


class ProviderError(KnowledgeEngineError):
    """A provider call failed."""


class ProviderUnavailable(ProviderError):
    """Transient provider failure (429/5xx/model-removed) — eligible for failover."""


class ProviderExhausted(ProviderError):
    """Every provider in the cascade failed; message should dead-letter."""


class RateLimitTimeout(ProviderError):
    """A token-bucket wait exceeded its deadline."""


class ConfigurationError(KnowledgeEngineError):
    """Missing/invalid config."""


class SearchError(KnowledgeEngineError):
    """A federated search source failed."""


class ScrapeError(KnowledgeEngineError):
    """Web scraping/extraction failed."""
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
    """Web scraping/extraction failed.

    Attributes:
        transient: Whether a retry may succeed (429/503/timeout).
    """

    transient: bool = False

    def __init__(self, message: str = "", *, transient: bool = False) -> None:
        """Create a ScrapeError.

        Args:
            message: Failure message.
            transient: Whether a retry may succeed (429/503/timeout).
        """
        super().__init__(message)
        self.transient = transient


class ScrapeSkipped(ScrapeError):
    """URL outside this adapter's remit (not a failure).

    Source-specific adapters (arXiv/Europe PMC full text) raise this before
    any network call for URLs they will never handle, so the router moves on
    quietly (debug log) instead of warning on every unrelated URL.
    """

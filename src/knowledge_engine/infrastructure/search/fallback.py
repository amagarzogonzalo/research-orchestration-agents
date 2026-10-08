"""Ordered general-web fallback chain with per-provider isolation.

Unlike the scholarly demux (parallel fan-out over free APIs), the general-web
tier mixes keyless and metered providers, so it runs *sequentially*: the next
provider is tried only when the previous one errors, is quota-exhausted, is
breaker-open, or returns nothing. That keeps a single query from spending a
credit at every provider. Mirrors the ScrapeRouter's ordered-chain pattern.

The search family must not import sibling adapter families (tach boundary), so
the small in-process breaker below stands in for the Redis-backed
``infrastructure.circuit.CircuitBreaker``; callers may still inject any object
exposing the same ``allow``/``record_success``/``record_failure`` surface.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from knowledge_engine.core.exceptions import QuotaExhausted
from knowledge_engine.core.ports import SearchPort
from knowledge_engine.observability import telemetry
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)


class _LocalBreaker:
    """Minimal in-process circuit breaker."""

    def __init__(self, *, threshold: int = 3, cooldown: float = 60.0) -> None:
        """Create a _LocalBreaker.

        Args:
            threshold: Consecutive failures before opening.
            cooldown: Seconds the breaker stays open.
        """
        self.threshold = threshold
        self.cooldown = cooldown
        self._fails = 0
        self._open_until = 0.0

    async def allow(self) -> bool:
        """Whether a call is permitted right now.

        Returns:
            bool: True when the breaker is closed (or the cooldown elapsed).
        """
        return self._open_until <= time.time()

    async def record_success(self) -> None:
        """Reset the failure count after a successful call."""
        self._fails = 0

    async def record_failure(self) -> None:
        """Count a failure, opening the breaker at the threshold."""
        self._fails += 1
        if self._fails >= self.threshold:
            self._open_until = time.time() + self.cooldown
            self._fails = 0


class FallbackSearchAdapter(SearchPort):
    """Sequential provider chain; first acceptable result wins.

    Attributes:
        name: Adapter name surfaced to the demux logger.
    """

    name = "general_web"

    def __init__(self, providers: list[SearchPort], *, breakers: dict[str, Any] | None = None) -> None:
        """Create a FallbackSearchAdapter.

        Args:
            providers: Ordered provider chain (first = highest priority).
            breakers: Optional per-provider breakers keyed by provider name;
                local in-process breakers are built when omitted.

        Raises:
            ValueError: When ``providers`` is empty.
        """
        if not providers:
            raise ValueError("FallbackSearchAdapter needs at least one provider")
        self.providers = providers
        self._breakers: dict[str, Any] = breakers or {
            getattr(provider, "name", type(provider).__name__): _LocalBreaker() for provider in providers
        }

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Try each provider in order; return the first non-empty result set.

        A provider that is quota-exhausted is skipped without a network call
        (the adapter checks its own ledger). Any other failure trips that
        provider's breaker and moves on. Every attempt emits a span +
        counter with the outcome, so quality gaps localize instantly
        (issue #21). Returns ``[]`` when every provider fails or returns
        nothing.

        Args:
            query: Search terms.
            limit: Maximum records to request per provider.

        Returns:
            list[SourceRecord]: Results from the first successful provider.
        """
        for provider in self.providers:
            name = getattr(provider, "name", type(provider).__name__)
            breaker = self._breakers.get(name)
            with telemetry.span(f"search.{name}", query=query) as current:
                if breaker is not None and not await breaker.allow():
                    logger.info("general-web: skipping %s (breaker open)", name)
                    current.set_attribute("outcome", "breaker_open")
                    telemetry.search_provider_calls.add(1, {"provider": name, "outcome": "breaker_open"})
                    continue
                try:
                    records = await provider.search(query, limit=limit)
                except QuotaExhausted as exc:
                    logger.info("general-web: skipping %s (quota): %s", name, exc)
                    current.set_attribute("outcome", "quota")
                    telemetry.search_provider_calls.add(1, {"provider": name, "outcome": "quota"})
                    continue
                except Exception as exc:  # noqa: BLE001 - isolate one provider from the chain
                    logger.warning("general-web: %s failed: %s", name, exc)
                    if breaker is not None:
                        await breaker.record_failure()
                    current.record_exception(exc)
                    current.set_attribute("outcome", "failure")
                    telemetry.search_provider_calls.add(1, {"provider": name, "outcome": "failure"})
                    continue
                if not records:
                    logger.info("general-web: %s returned no results; trying next", name)
                    current.set_attribute("outcome", "empty")
                    telemetry.search_provider_calls.add(1, {"provider": name, "outcome": "empty"})
                    continue
                if breaker is not None:
                    await breaker.record_success()
                current.set_attribute("outcome", "success")
                current.set_attribute("results", len(records))
                telemetry.search_provider_calls.add(1, {"provider": name, "outcome": "success"})
                return records
        return []

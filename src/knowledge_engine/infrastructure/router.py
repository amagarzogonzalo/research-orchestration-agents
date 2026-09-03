"""Multi-provider fallback router implementing ProviderPort (ADR 0004).

Combines affinity-based primary selection, an ordered failover cascade, per-provider
circuit breakers, and the token-bucket gate from :mod:`.rate_limit`. Provides
Instructor-backed structured extraction against any provider adapter.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from knowledge_engine.core.exceptions import (
    ProviderExhausted,
    ProviderUnavailable,
    RateLimitTimeout,
)
from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult, UsageSummary
from knowledge_engine.infrastructure.circuit import CircuitBreaker
from knowledge_engine.infrastructure.rate_limit import TokenBucketLimiter
from knowledge_engine.observability.telemetry import tracer

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Fallback order per affinity class: primary first, cheap next, catch-all last.
_CASCADE: dict[Affinity, list[str]] = {
    Affinity.MICRO: ["Groq", "OpenRouter", "Ollama"],
    Affinity.MACRO: ["Gemini", "Mistral", "OpenRouter", "Ollama"],
    Affinity.MILLION: ["Mistral", "Gemini", "OpenRouter", "Ollama"],
    Affinity.FALLBACK: ["OpenRouter", "Ollama"],
}

# Transient failures that trigger a cascade/hop.
_TRANSIENT = (ProviderUnavailable, RateLimitTimeout)


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, _TRANSIENT)


class BaseProviderAdapter:
    """Minimal interface concrete providers implement."""

    name = "base"
    affinity: Affinity | None = None  # None = usable in every cascade

    async def complete(self, req: ChatRequest) -> ChatResult:  # pragma: no cover - interface
        raise NotImplementedError

    async def extract(self, req: ExtractionRequest, response_model) -> object:  # pragma: no cover
        raise NotImplementedError


class FallbackRouter(ProviderPort):
    def __init__(
        self,
        adapters: list[BaseProviderAdapter],
        *,
        rc: "Redis | None" = None,
        limiter: TokenBucketLimiter | None = None,
        breaker_threshold: int = 3,
        breaker_cooldown: float = 60.0,
    ) -> None:
        self.adapters = {a.name: a for a in adapters}
        self._rc = rc
        self._limiter = limiter or TokenBucketLimiter(rc)
        self._breakers = {
            name: CircuitBreaker(rc, name, threshold=breaker_threshold, cooldown=breaker_cooldown)
            for name in self.adapters
        }

    def _cascade(self, affinity: Affinity) -> list[BaseProviderAdapter]:
        names = _CASCADE.get(affinity, _CASCADE[Affinity.FALLBACK])
        return [self.adapters[n] for n in names if n in self.adapters]

    async def complete(self, req: ChatRequest) -> ChatResult:
        with tracer.start_as_current_span("router.complete") as span:
            span.set_attribute("gen_ai.request.model", req.affinity.value)
            last: BaseProviderAdapter | None = None
            for adapter in self._cascade(req.affinity):
                last = adapter
                if not await self._breakers[adapter.name].allow():
                    logger.info("breaker open, skipping %s", adapter.name)
                    continue
                try:
                    await self._limiter.acquire(adapter.name, timeout=30.0)
                    result = await self._retried_call(adapter, req)
                    await self._breakers[adapter.name].record_success()
                    return result
                except _TRANSIENT as exc:
                    logger.warning("provider %s transient failure: %s", adapter.name, exc)
                    await self._breakers[adapter.name].record_failure()
                    continue
            raise ProviderExhausted(f"all providers failed for affinity={req.affinity!s}; last={last.name if last else None}")

    @retry(
        wait=wait_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )
    async def _retried_call(self, adapter: BaseProviderAdapter, req: ChatRequest) -> ChatResult:
        return await adapter.complete(req)

    async def extract(self, req: ExtractionRequest) -> object:
        import instructor  # heavy, only needed for structured extraction (lazy)

        response_model = _import_model(req.schema_ref)
        # Use the affinity-appropriate adapter; extraction is structured + retried.
        for adapter in self._cascade(req.affinity or Affinity.MACRO):
            if not await self._breakers[adapter.name].allow():
                continue
            try:
                return await self._extract_retried(adapter, req, response_model)
            except _TRANSIENT:
                await self._breakers[adapter.name].record_failure()
                continue
        raise ProviderExhausted("no provider served extraction")

    @retry(
        wait=wait_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )
    async def _extract_retried(self, adapter: BaseProviderAdapter, req: ExtractionRequest, response_model) -> object:
        return await adapter.extract(req, response_model)


def _import_model(dotted: str) -> type:
    """Import a Pydantic model by dotted path, e.g. 'knowledge_engine.schemas.source.SourceRecord'."""
    module_path, _, attr = dotted.rpartition(".")
    module = inspect.import_module(module_path)
    model = getattr(module, attr)
    if not isinstance(model, type):
        raise TypeError(f"{dotted} is not a class")
    return model


# Re-export for tester convenience.
def make_usage(input_tokens: int, output_tokens: int, *, provider: str = "test", model: str = "test") -> UsageSummary:
    from knowledge_engine.core.pricing import compute_cost_usd

    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=compute_cost_usd(provider, model, input_tokens, output_tokens),
    )
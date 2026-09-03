"""Tests for the fallback router + circuit breaker (ADR 0004). Uses fakes; no network/REDIS."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import ProviderExhausted, ProviderUnavailable
from knowledge_engine.infrastructure.rate_limit import TokenBucketLimiter
from knowledge_engine.infrastructure.router import BaseProviderAdapter, FallbackRouter
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult, UsageSummary


class FakeAdapter(BaseProviderAdapter):
    def __init__(self, name: str, *, fail: bool = False, affinity=None) -> None:
        self.name = name
        self.fail = fail
        self.affinity = affinity
        self.calls = 0

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable(f"{self.name} is down")
        big = UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=0.0)
        return ChatResult.from_response("ok", provider=self.name, model=self.name, usage=big)

    async def extract(self, req, response_model):
        return None


def _req(affinity: Affinity = Affinity.MACRO) -> ChatRequest:
    return ChatRequest.of("system", "hi", affinity=affinity, max_tokens=8)


async def test_router_uses_primary() -> None:
    primary = FakeAdapter("Groq", affinity=Affinity.MICRO)
    secondary = FakeAdapter("OpenRouter", affinity=Affinity.FALLBACK)
    router = FallbackRouter([primary, secondary], limiter=TokenBucketLimiter(rc=None))
    res = await router.complete(_req(Affinity.MICRO))
    assert res.provider == "Groq"
    assert primary.calls == 1


async def test_router_fails_over_on_transient_error() -> None:
    primary = FakeAdapter("Groq", fail=True, affinity=Affinity.MICRO)
    secondary = FakeAdapter("OpenRouter", affinity=Affinity.FALLBACK)
    router = FallbackRouter([primary, secondary], limiter=TokenBucketLimiter(rc=None))
    res = await router.complete(_req(Affinity.MICRO))
    assert res.provider == "OpenRouter"


async def test_router_exhausts_all_providers() -> None:
    primary = FakeAdapter("Groq", fail=True, affinity=Affinity.MICRO)
    secondary = FakeAdapter("OpenRouter", fail=True, affinity=Affinity.FALLBACK)
    router = FallbackRouter([primary, secondary], limiter=TokenBucketLimiter(rc=None))
    with pytest.raises(ProviderExhausted):
        await router.complete(_req(Affinity.MICRO))


async def test_circuit_breaker_skips_sick_provider() -> None:
    primary = FakeAdapter("Groq", fail=True, affinity=Affinity.MICRO)
    secondary = FakeAdapter("OpenRouter", affinity=Affinity.FALLBACK)
    limiter = TokenBucketLimiter(rc=None)
    router = FallbackRouter([primary, secondary], limiter=limiter, breaker_threshold=2, breaker_cooldown=60.0)
    # Two failures: first trips Groq's failure counter to 1, second to 2 -> breaker OPEN.
    await router.complete(_req(Affinity.MICRO))  # Groq fails(1) -> OpenRouter ok
    await router.complete(_req(Affinity.MICRO))  # Groq fails(2) -> breaker opens -> OpenRouter ok
    primary_calls_before = primary.calls
    # Third call must NOT attempt Groq (breaker open) — just route to OpenRouter.
    res = await router.complete(_req(Affinity.MICRO))
    assert res.provider == "OpenRouter"
    assert primary.calls == primary_calls_before  # Groq was skipped, no new attempt
---
@id: "plans/0004"
type: ADR
status: ACCEPTED
date: 2026-09-03
related:
  - 0003
  - 0001
---

# 0004 — Multi-Provider Fallback Router (Circuit Breaker)

## Context

Free tiers have **zero SLA**. Model IDs vanish (OpenRouter `404` on removed models), endpoints throttle (`429`), and a single provider going hot can starve the whole pipeline. We need automatic failover that preserves *deterministic routing decisions* and never blocks forever on a dead endpoint.

## Decision

A single `FallbackRouter` implements the `ProviderPort` (`0001`) and combines:

1. **Affinity-based primary selection** (which provider is *best* for this task class, not random).
2. **Ordered failover cascade** across providers.
3. **Per-provider circuit breaker** (Redis-backed) to skip known-sick endpoints.
4. **Bounded retry** honoring 429/Retry-After + exponential-backoff via `tenacity`.

## Affinity map (compute arbitrage, from `docs/information.md`)

| `affinity` | Primary | Rationale |
|-----------|---------|-----------|
| `micro` | Groq | ultra-low-latency boolean filtering / entity extraction |
| `macro` | Gemini Flash | high-context aggregation, mid-document synthesis |
| `million` | Mistral Experiment | massive token volume (bulk parse) |
| `fallback` | OpenRouter free 28+ models | no-SLA; last resort before Ollama |

Secondary cascade order per class: Primary -> next cheap -> `OpenRouter` -> `Ollama` (local) -> return `ProviderExhausted`.

## Circuit breaker (Redis-backed)

Per provider, keyed `breaker:{provider}`, track consecutive-failure + a cooldown window. When open, the router **skips** that provider for `cooldown` seconds, freeing the bucket.

```python
import time
import redis.asyncio as redis

class RedisCircuitBreaker:
    def __init__(self, rc, name, threshold=3, cooldown=60.0):
        self.rc = rc; self.name = name
        self.tkey = f"breaker:{name}:trip"
        self.fkey = f"breaker:{name}:fails"

    async def allow(self) -> bool:
        trip = await self.rc.get(self.tkey)
        if trip and float(trip) > time.time():
            return False               # open -> skip provider
        return True

    async def record_failure(self):
        await self.rc.incr(self.fkey)
        fails = int(await self.rc.get(self.fkey) or 0)
        if fails >= THRESHOLD:
            await self.rc.setex(self.tkey, COOLDOWN, time.time() + COOLDOWN)
            await self.rc.delete(self.fkey)

    async def record_success(self):
        await self.rc.delete(self.fkey)
```

## Router + worker orchestration

```python
class FallbackRouter(ProviderPort):
    def __init__(self, providers: dict[str, ProviderAdapter], rc, thresholds):
        self.providers = providers
        self.breakers = {p: RedisCircuitBreaker(rc, p) for p in providers}

    async def complete(self, req: ChatRequest, bucket) -> ChatResult:
        for name in self.cascade_for(req.affinity):        # ordered by affinity+cost
            if not await self.breakers[name].allow():
                continue                                     # skip sick endpoint
            try:
                # gate on the provider's token bucket (0003)
                await bucket.acquire(name)
                res = await self.providers[name].complete(req)
                await self.breakers[name].record_success()
                return res
            except RateLimitTimeout:
                pass                                        # exhausted bucket; try next
            except ProviderUnavailable as e:                # 429/5xx/model-removed
                await self.breakers[name].record_failure()
                continue                                    # cascade
        raise ProviderExhausted(req)
```

Bound retry policy per attempt via `tenacity`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3),
       retry=retry_if_exception_type((429, 503)), reraise=True)
async def guarded_call(adapter, req):
    ...
```

## Edge cases hardened
- **Model removed (`404`)**: recorded as failure; cascade skips that *model id*, not the whole host (OpenRouter can serve others).
- **`Retry-After` header**: prefer parsed value over generic backoff when present.
- **Ollama local final fallback**: guarantees availability at $0 but with weakest quality — it is the last resort, and low-`affinity` tasks should rarely reach it.
- **No infinite loop**: `ProviderExhausted` is a terminal, observable failure that dead-letters the message with its `attempt` count.

## References
- `docs/instruction.md` §Multi-Tiered LLM Fallback Routing; `docs/information.md` compute-arbitrage + "Redis-backed API Gateway".
- Reference impl: `BerriAI/litellm` router (58k★); we reimplement minimal subset to stay dependency-light and free.

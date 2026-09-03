---
@id: "plans/0003"
type: ADR
status: ACCEPTED
date: 2026-09-03
related:
  - 0004
---

# 0003 — Token-Bucket Rate Limiting & Backpressure

## Context

Free-tier endpoints are defined by hard RPM limits: OpenRouter ~20 RPM, OpenAlex credit-metered searches, Semantic Scholar's shared 1k req/s (observed live `429` this session), Groq/Mistral per-minute caps. Synchronous, naive dispatch is catastrophic: one burst trips the breaker and poisons the whole pipeline. We need **asymmetric backpressure** that decouples ingestion from the failure point.

## Decision

Model every outbound endpoint with an independent **token-bucket queue** (bucket = quota, capacity = burst*, refill = RPM), expressed as a Redis-backed limiter shared across workers. The broker (`0001`) supplies the queue; the limiter gates the worker's call to the provider/router.

Key properties:
- **Burst allowance** = *unused* capacity accumulates, so a quiet spell permits a controlled burst (cheap wins on polling APIs like OpenAlex DOI lookups).
- **Global + per-provider keys** so one hot worker cannot exhaust a shared public quota (Semantic Scholar).
- **Delay, don't drop** by default. Only the circuit breaker (0004) permanently drops.

## Algorithm (Redis-backed, atomic)

```python
import time
import asyncio
import redis.asyncio as redis

class TokenBucket:
    """Redis Lua-backed token bucket. capacity=burst, refill_rate=tokens/sec."""
    LUA = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local refill = tonumber(ARGV[3])
    local allow = tonumber(ARGV[4])
    local st = redis.call('HMGET', key, 'tokens', 'ts')
    local tokens = tonumber(st[1]) or capacity
    local ts = tonumber(st[2]) or now
    tokens = math.min(capacity, tokens + (now - ts) * refill)
    if tokens >= allow then
        tokens = tokens - allow
        redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
        return {1, tokens}
    end
    return {0, tokens}
    """

    def __init__(self, rc, key, capacity, refill_per_sec):
        self.rc, self.key, self.cap, self.refill = rc, key, capacity, refill_per_sec
        self.script = rc.register_script(self.LUA)

    async def acquire(self, cost=1, timeout=30.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            allowed, tokens = await self.script(
                keys=[self.key], args=[time.time(), self.cap, self.refill, cost]
            )
            if allowed: return tokens
            if asyncio.get_event_loop().time() > deadline: raise RateLimitTimeout(self.key)
            await asyncio.sleep(0.05)

# configuration (per provider), e.g.:
LIMITS = {
    "OpenRouter":  TokenConfig(capacity=20,  refill_per_sec=20/60.0),  # ~20 RPM
    "OpenAlex":    TokenConfig(capacity=50,  refill_per_sec=100000/86400.0), # daily credits-ish
    "SemanticScholar": TokenConfig(capacity=100, refill_per_sec=1000/60.0),
    "Gemini":      TokenConfig(capacity=60,  refill_per_sec=60/60.0),
    "Groq":        TokenConfig(capacity=30,  refill_per_sec=30/60.0),
    "Mistral":     TokenConfig(capacity=40,  refill_per_sec=40/60.0),
}
```

## Broker integration (worker flow)

1. Consumer reads stream `inbox.<provider>` event (payload is validation-completed `ChatRequest` — see `0002`).
2. `await bucket.acquire()` — queues locally / with bounded wait.
3. If `RateLimitTimeout`: write event back to `requeue.<provider>` with `attempt+1` (bounded) and `Continue` to breaker for re-admission; do **not** hammer.
4. On success: `XACK`, emit `done` event with `UsageSummary`.

## Backpressure / overflow semantics

- **Graceful queue flush**: the bucket's token ledger is the governor; stream `inbox` depth is secondary.
- **Never drop**: all overflow returns to `inbox`/`dead-letter` with `error=RateLimitTimeout` and a monotonic attempt counter. `errored` path only dead-letters after `max_attempts` (default 3) to avoid infinite loops.

## Edge cases hardened
- **Negative refill windows** (clock skew) — clamp `now - ts` to `>= 0`.
- **Redis failure**: fall back to an in-process `asyncio`-based token bucket (degraded-but-safer) so the pipeline degrades to a single-node limiter rather than crashing.
- **Precision**: Lua guarantees atomic check-and-decrement; no TOCTOU between `has-token` and `use-token`.

## References
- `docs/information.md` typed 20 RPM OpenRouter and strict provider limits.
- Pattern: classic token bucket; Redis Lua single-node atomicity avoids a paid distributed-limiter.
"""Token-bucket rate limiting (ADR 0003).

Redis-backed (Lua atomic check-and-decrement) with an in-process asyncio
fallback so the pipeline degrades to single-node limiting during a Redis outage
instead of crashing. Delay, don't drop — only the circuit breaker drops.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from knowledge_engine.core.exceptions import RateLimitTimeout

# Bucket config per provider: (capacity=burst, refill_per_second).
TOKEN_BUDGETS: dict[str, tuple[int, float]] = {
    "OpenRouter": (20, 20 / 60.0),  # ~20 RPM
    "OpenAlex": (100, 100_000 / 86_400.0),  # daily-credit-approximated
    "SemanticScholar": (100, 1000 / 60.0),  # shared 1k req/s public limit
    "Gemini": (60, 60 / 60.0),
    "Groq": (30, 30 / 60.0),
    "Mistral": (40, 40 / 60.0),
    "Ollama": (50, 50 / 60.0),
}

_BUCKET_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local st = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(st[1]) or capacity
local ts = tonumber(st[2]) or now
local dt = now - ts
if dt < 0 then dt = 0 end
tokens = math.min(capacity, tokens + dt * refill)
if tokens >= cost then
    tokens = tokens - cost
    redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
    return {1, tokens}
end
return {0, tokens}
"""


@dataclass
class TokenBucketConfig:
    capacity: int
    refill_per_second: float


class AsyncTokenBucket:
    """In-process fallback bucket (no Redis). Thread/loop-safe within one process."""

    def __init__(self, key: str, config: TokenBucketConfig) -> None:
        self.key = key
        self.capacity = config.capacity
        self.refill = config.refill_per_second
        self._tokens = float(config.capacity)
        self._ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def _tokens_now(self) -> float:
        now = time.monotonic()
        dt = max(now - self._ts, 0.0)
        return min(self.capacity, self._tokens + dt * self.refill)

    async def acquire(self, cost: int = 1, timeout: float = 30.0) -> float:
        async with self._lock:
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                tokens = await self._tokens_now()
                if tokens >= cost:
                    self._tokens = tokens - cost
                    self._ts = time.monotonic()
                    return self._tokens
                if asyncio.get_event_loop().time() > deadline:
                    raise RateLimitTimeout(self.key)
                await asyncio.sleep(0.05)


class TokenBucketLimiter:
    """Facade: Redis-backed if available, else in-process. Keyed per provider."""

    def __init__(self, rc: aioredis.Redis | None = None, budgets: dict[str, tuple[int, float]] | None = None) -> None:
        self._budgets = {k: TokenBucketConfig(cap, refill) for k, (cap, refill) in (budgets or TOKEN_BUDGETS).items()}
        self._rc = rc
        self._local: dict[str, AsyncTokenBucket] = {}
        self._scripts = {}

    def _bucket_for(self, provider: str) -> AsyncTokenBucket:
        cfg = self._config_for(provider)
        if provider not in self._local:
            self._local[provider] = AsyncTokenBucket(provider, cfg)
        return self._local[provider]

    def _config_for(self, provider: str) -> TokenBucketConfig:
        default = TokenBucketConfig(capacity=20, refill_per_second=0.0)
        return self._budgets.get(provider, default)

    async def acquire(self, provider: str, cost: int = 1, timeout: float = 30.0) -> float:
        """Consume `cost` tokens from `provider`'s bucket, waiting up to `timeout`."""
        cfg = self._config_for(provider)
        if self._rc is not None:
            try:
                script = self._scripts.get(provider)
                if script is None:
                    script = self._rc.register_script(_BUCKET_LUA)
                    self._scripts[provider] = script
                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    allowed, tokens = await script(
                        keys=[f"bucket:{provider}"],
                        args=[time.time(), cfg.capacity, cfg.refill_per_second, cost],
                    )
                    if allowed:
                        return float(tokens)
                    if asyncio.get_event_loop().time() > deadline:
                        raise RateLimitTimeout(provider)
                    await asyncio.sleep(0.05)
            except RateLimitTimeout:
                raise
            except Exception:
                # Redis unavailable -> degrade to in-process bucket.
                pass
        return await self._bucket_for(provider).acquire(cost=cost, timeout=timeout)

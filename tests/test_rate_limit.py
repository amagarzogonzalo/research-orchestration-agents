"""Tests for the token-bucket rate limiter (ADR 0003). Uses the in-process bucket."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import RateLimitTimeout
from knowledge_engine.infrastructure.rate_limit import AsyncTokenBucket, TokenBucketConfig, TokenBucketLimiter


async def test_bucket_consumes_tokens() -> None:
    bucket = AsyncTokenBucket("p", TokenBucketConfig(capacity=3, refill_per_second=0.5))
    await bucket.acquire()  # 3 -> 2
    await bucket.acquire()  # 2 -> 1
    remaining = await bucket.acquire(cost=1)  # 1 -> ~0 (tiny refill epsilon in flight)
    assert remaining < 1  # drained, allowing only sub-unit refill


async def test_bucket_refills() -> None:
    bucket = AsyncTokenBucket("p", TokenBucketConfig(capacity=1, refill_per_second=1000.0))
    await bucket.acquire()  # empty
    # fast refill allows immediate reuse
    await bucket.acquire()
    # no assertion -> just must not raise


async def test_bucket_times_out() -> None:
    bucket = AsyncTokenBucket("p", TokenBucketConfig(capacity=1, refill_per_second=0.0))
    await bucket.acquire()
    with pytest.raises(RateLimitTimeout):
        await bucket.acquire(timeout=0.1)


async def test_limiter_without_redis_works() -> None:
    limiter = TokenBucketLimiter(rc=None, budgets={"X": (5, 1000.0)})
    for _ in range(5):
        await limiter.acquire("X", timeout=1.0)
    # bucket drained but refills fast; a sixth acquire must still eventually pass
    await limiter.acquire("X", timeout=1.0)
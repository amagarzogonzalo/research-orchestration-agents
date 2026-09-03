"""Per-provider circuit breaker (ADR 0004).

Redis-backed; skips a sick provider for a cooldown window so the token bucket
isn't drained retrying a dead endpoint. Falls back to in-process state on a
Redis outage.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis


class CircuitBreaker:
    def __init__(self, rc: aioredis.Redis | None, name: str, *, threshold: int = 3, cooldown: float = 60.0) -> None:
        self.rc = rc
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self._trip_key = f"breaker:{name}:trip"
        self._fail_key = f"breaker:{name}:fails"
        # In-process fallback state.
        self._local_trip: float = 0.0
        self._local_fails = 0

    async def allow(self) -> bool:
        if self.rc is not None:
            try:
                trip = await self.rc.get(self._trip_key)
                if trip and float(trip) > time.time():
                    return False
                return True
            except Exception:
                pass
        return self._local_trip < time.time()

    async def record_success(self) -> None:
        if self.rc is not None:
            try:
                await self.rc.delete(self._fail_key)
                return
            except Exception:
                pass
        self._local_fails = 0

    async def record_failure(self) -> None:
        if self.rc is not None:
            try:
                fails = await self.rc.incr(self._fail_key)
                if fails >= self.threshold:
                    await self.rc.setex(self._trip_key, self.cooldown, time.time() + self.cooldown)
                    await self.rc.delete(self._fail_key)
                return
            except Exception:
                pass
        self._local_fails += 1
        if self._local_fails >= self.threshold:
            self._local_trip = time.time() + self.cooldown
            self._local_fails = 0
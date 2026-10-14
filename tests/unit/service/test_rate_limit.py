"""Unit tests: the sliding-window rate limiter (caveat C)."""

from __future__ import annotations

from knowledge_engine.service.rate_limit import RateLimiter


def test_allows_up_to_the_limit_then_blocks() -> None:
    """The Nth+1 hit within the window is refused."""
    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("k", now=0.0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("k", now=0.0) is False


def test_window_slides() -> None:
    """Hits older than 60 s leave the window."""
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("k", now=0.0) is True
    assert limiter.allow("k", now=30.0) is False
    assert limiter.allow("k", now=61.0) is True


def test_keys_are_independent() -> None:
    """One key's traffic does not consume another's budget."""
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("b", now=0.0) is True


def test_disabled_when_non_positive() -> None:
    """per_minute <= 0 disables the limiter entirely."""
    limiter = RateLimiter(per_minute=0)
    assert limiter.enabled is False
    assert all(limiter.allow("k", now=0.0) for _ in range(100))

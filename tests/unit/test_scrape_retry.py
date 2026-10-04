"""Unit tests: scrape retry/backoff on transient failures (offline, fakes)."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.scrape_router import ScrapeRouter
from knowledge_engine.schemas.source import DocumentBody, FetchMethod


def test_scrape_error_defaults_to_permanent() -> None:
    assert ScrapeError("boom").transient is False


def test_scrape_error_carries_transient_flag() -> None:
    assert ScrapeError("rate limited", transient=True).transient is True


class FlakyScraper(ScrapePort):
    """Fails transiently N times, then serves a body."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def to_markdown(self, url: str) -> DocumentBody:
        self.calls += 1
        if self.calls <= self.failures:
            raise ScrapeError("429 busy", transient=True)
        return DocumentBody(url=url, title="t", markdown="x" * 300, crawl_method=FetchMethod.JINA)


class DeadScraper(ScrapePort):
    """Always fails permanently."""

    def __init__(self) -> None:
        self.calls = 0

    async def to_markdown(self, url: str) -> DocumentBody:
        self.calls += 1
        raise ScrapeError("404 gone")


async def test_router_retries_transient_failures() -> None:
    router = ScrapeRouter(chain=[FlakyScraper(failures=2)])
    body = await router.to_markdown("https://example.org")
    assert body.title == "t"
    assert router.chain[0].calls == 3


async def test_router_skips_permanent_failures_without_retry() -> None:
    dead, fallback = DeadScraper(), FlakyScraper(failures=0)
    router = ScrapeRouter(chain=[dead, fallback])
    body = await router.to_markdown("https://example.org")
    assert body.title == "t"
    assert dead.calls == 1


async def test_router_gives_up_after_retries() -> None:
    router = ScrapeRouter(chain=[FlakyScraper(failures=99)])
    with pytest.raises(ScrapeError):
        await router.to_markdown("https://example.org")

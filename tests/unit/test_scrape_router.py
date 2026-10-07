"""Unit tests: ScrapeRouter ordered fallback chain + content-quality guard."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import ScrapeError, ScrapeSkipped
from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.infrastructure.scrape.playwright_adapter import PlaywrightAdapter
from knowledge_engine.infrastructure.scrape.scrape_router import ScrapeRouter, looks_clean
from knowledge_engine.schemas.source import DocumentBody, FetchMethod

_GOOD = (
    "Attention mechanisms form the backbone of modern language models. "
    "This paper surveys sparse attention methods for efficient long-context inference "
    "and compares them across efficiency, quality, and implementation complexity."
) * 3


class FakeGoodScraper(ScrapePort):
    async def to_markdown(self, url: str) -> DocumentBody:
        return DocumentBody(url=url, title="good", markdown=_GOOD, crawl_method=FetchMethod.CRAWL4AI)


class FakeBadScraper(ScrapePort):
    async def to_markdown(self, url: str) -> DocumentBody:
        raise ScrapeError("down")


class FakeEmptyScraper(ScrapePort):
    async def to_markdown(self, url: str) -> DocumentBody:
        return DocumentBody(url=url, title="empty", markdown="   ", crawl_method=FetchMethod.TRAFILATURA)


class FakeBinaryScraper(ScrapePort):
    async def to_markdown(self, url: str) -> DocumentBody:
        return DocumentBody(
            url=url,
            title="binary",
            markdown="\x00\x00binary\x00pdf-dump\x00\x00" * 40,
            crawl_method=FetchMethod.TRAFILATURA,
        )


class FakeSkipScraper(ScrapePort):
    """URL outside remit: the chain must move on quietly, not warn-fail."""

    async def to_markdown(self, url: str) -> DocumentBody:
        raise ScrapeSkipped("not mine")


async def test_router_uses_first_success() -> None:
    primary = FakeGoodScraper()
    router = ScrapeRouter(chain=[FakeBadScraper(), primary, FakeEmptyScraper()])
    body = await router.to_markdown("https://a.org")
    assert body.markdown == _GOOD
    assert body.crawl_method is FetchMethod.CRAWL4AI


async def test_router_skips_empty_extraction() -> None:
    router = ScrapeRouter(chain=[FakeEmptyScraper(), FakeGoodScraper()])
    body = await router.to_markdown("https://b.org")
    assert body.markdown == _GOOD


async def test_router_skips_binary_pdf_dump() -> None:
    router = ScrapeRouter(chain=[FakeBinaryScraper(), FakeGoodScraper()])
    body = await router.to_markdown("https://pdf.org")
    assert body.markdown == _GOOD  # binary result treated as a miss


async def test_router_exhausts_chain_and_raises() -> None:
    router = ScrapeRouter(chain=[FakeBadScraper(), FakeBinaryScraper()])
    with pytest.raises(ScrapeError):
        await router.to_markdown("https://c.org")


async def test_router_skips_remit_mismatch_quietly() -> None:
    router = ScrapeRouter(chain=[FakeSkipScraper(), FakeGoodScraper()])
    body = await router.to_markdown("https://d.org")
    assert body.markdown == _GOOD


async def test_router_all_skipped_still_raises() -> None:
    router = ScrapeRouter(chain=[FakeSkipScraper()])
    with pytest.raises(ScrapeError):
        await router.to_markdown("https://e.org")


class FakeJinaScraper(ScrapePort):
    async def to_markdown(self, url: str) -> DocumentBody:
        return DocumentBody(url=url, title="remote", markdown=_GOOD, crawl_method=FetchMethod.JINA)


async def test_router_falls_through_to_last_resort() -> None:
    router = ScrapeRouter(chain=[FakeBadScraper(), FakeEmptyScraper(), FakeJinaScraper()])
    body = await router.to_markdown("https://blocked.org")
    assert body.markdown == _GOOD
    assert body.crawl_method is FetchMethod.JINA


def test_router_requires_nonempty_chain() -> None:
    with pytest.raises(ValueError):
        ScrapeRouter(chain=[])


def test_playwright_adapter_lazily_imports() -> None:
    """Construction must not import playwright (kept optional)."""
    adapter = PlaywrightAdapter()
    assert adapter.wait_selector == "body"


def test_looks_clean_accepts_real_text() -> None:
    assert looks_clean(_GOOD) is True


def test_looks_clean_rejects_short_text() -> None:
    assert looks_clean("hello") is False
    assert looks_clean("   ") is False


def test_looks_clean_rejects_nul_bytes() -> None:
    assert looks_clean("a\x00b" * 100) is False


def test_looks_clean_rejects_mojibake() -> None:
    assert looks_clean("\ufffd\ufffd\ufffd garbage text here " * 10) is False

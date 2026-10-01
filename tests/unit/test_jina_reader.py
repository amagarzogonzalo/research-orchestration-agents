"""Unit tests: JinaReaderAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import ScrapeError
from knowledge_engine.infrastructure.scrape.jina_reader import JinaReaderAdapter
from knowledge_engine.schemas.source import FetchMethod

_BODY = "Title: Example\n\nURL Source: https://example.com/\n\nMarkdown Content:\n# Hello\n\nClean text."


def _adapter(handler, **kwargs) -> JinaReaderAdapter:
    adapter = JinaReaderAdapter(**kwargs)
    headers = {"User-Agent": "knowledge-engine"}
    if adapter.api_key:
        headers["Authorization"] = f"Bearer {adapter.api_key}"

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=headers)

    adapter._client = _client
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "r.jina.ai"
    return httpx.Response(200, text=_BODY)


async def test_to_markdown_parses_envelope() -> None:
    body = await _adapter(_ok).to_markdown("https://example.com/")
    assert body.title == "Example"
    assert body.markdown == "# Hello\n\nClean text."
    assert body.crawl_method is FetchMethod.JINA


async def test_api_key_is_forwarded() -> None:
    async def _auth(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sekrit"
        return httpx.Response(200, text=_BODY)

    body = await _adapter(_auth, api_key="sekrit").to_markdown("https://example.com/")
    assert body.markdown.startswith("# Hello")


async def test_empty_content_raises_typed_error() -> None:
    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Title: X\n\nMarkdown Content:\n   ")

    with pytest.raises(ScrapeError):
        await _adapter(_empty).to_markdown("https://example.com/")


async def test_http_failure_raises_typed_error() -> None:
    async def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    with pytest.raises(ScrapeError):
        await _adapter(_boom).to_markdown("https://example.com/")

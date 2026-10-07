"""Unit tests: DuckDuckGoAdapter (offline, stubbed HTTP transport)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search.duckduckgo import DuckDuckGoAdapter, _ResultsParser, _unwrap
from knowledge_engine.schemas.source import SearchSource

_HTML = """
<html><body>
<div class="result">
  <a class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide&amp;rut=x">Local LLM Guide</a>
  <a class="result__snippet"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide">Run 70B models at home.</a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example.org/paper">Direct Paper</a>
  <a class="result__snippet" href="https://direct.example.org/paper">VRAM requirements analyzed.</a>
</div>
</body></html>
"""


def _adapter(handler) -> DuckDuckGoAdapter:
    adapter = DuckDuckGoAdapter()
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


def test_unwrap_resolves_redirect() -> None:
    """DDG redirect wrappers resolve to the real target."""
    assert _unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide&rut=x") == "https://example.org/guide"
    assert _unwrap("https://direct.example.org/paper") == "https://direct.example.org/paper"


def test_parser_collects_triples() -> None:
    """Titles, unwrapped URLs, and snippets pair up per result."""
    parser = _ResultsParser()
    parser.feed(_HTML)
    assert len(parser.results) == 2
    assert parser.results[0]["title"] == "Local LLM Guide"
    assert parser.results[0]["url"] == "https://example.org/guide"
    assert "at home" in parser.results[0]["snippet"]


async def test_search_maps_results() -> None:
    """Parsed results become DUCKDUCKGO source records in order."""

    async def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    records = await _adapter(_ok).search("local llm", limit=10)
    assert [r.id for r in records] == [
        "duckduckgo:https://example.org/guide",
        "duckduckgo:https://direct.example.org/paper",
    ]
    assert all(r.source is SearchSource.DUCKDUCKGO for r in records)
    assert records[0].abstract == "Run 70B models at home."
    assert records[0].pdf_url is None


async def test_search_respects_limit() -> None:
    """Only the first N results map."""

    async def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    assert len(await _adapter(_ok).search("local llm", limit=1)) == 1


async def test_search_empty_results_returns_empty_list() -> None:
    """Pages without results map to no records, not an error."""

    async def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>no results</body></html>")

    assert await _adapter(_empty).search("zzzznonexistent", limit=5) == []


async def test_search_raises_typed_error_on_http_failure() -> None:
    """Transport failures surface as SearchError for demux isolation."""

    async def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    with pytest.raises(SearchError):
        await _adapter(_boom).search("local llm")

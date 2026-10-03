"""Offline tests for the OpenAlex adapter (no network; MockTransport).

Live probe 2026-10-03: ``/works?search=pretraining+synthetic+data&per-page=1``
HTTP 200 with ``results[0].abstract_inverted_index`` shaped like
``{"Deep": [0, 98], ...}`` — the shape that crashed the old
``max(inverted.values())`` reconstruction (lists compared instead of
positions). The adapter sends ``filter=title.search:`` + ``mailto``.
"""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import SearchError
from knowledge_engine.infrastructure.search import openalex
from knowledge_engine.infrastructure.search.openalex import OpenAlexAdapter
from knowledge_engine.schemas.source import SearchSource

_inverted_to_text = openalex._inverted_to_text


def _works_payload() -> dict:
    """Build a minimal /works payload with real-shaped inverted abstracts."""
    return {
        "meta": {"count": 2},
        "results": [
            {
                "id": "https://openalex.org/W2954996726",
                "doi": "https://doi.org/10.1186/s40537-019-0197-0",
                "display_name": "A survey on Image Data Augmentation for Deep Learning",
                "authorships": [{"author": {"display_name": "Connor Shorten"}}],
                "publication_year": 2019,
                "cited_by_count": 13235,
                "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
                "abstract_inverted_index": {"Deep": [0], "learning": [1], "survey": [2]},
            },
            {
                "id": "https://openalex.org/W123",
                "doi": None,
                "display_name": "Untitled preprint",
                "authorships": [],
                "publication_year": None,
                "cited_by_count": 0,
                "best_oa_location": None,
                "abstract_inverted_index": None,
            },
        ],
    }


def _client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by a MockTransport handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_inverted_to_text_rebuilds_abstract() -> None:
    """Rebuild readable text from position lists."""
    text = _inverted_to_text({"Deep": [0, 3], "learning": [1], "survey": [2]})
    assert text == "Deep learning survey Deep"


def test_inverted_to_text_ignores_malformed_shapes() -> None:
    """Tolerate non-list values and empty lists without crashing."""
    assert _inverted_to_text({"ok": [0], "bad": "nope", "empty": []}) == "ok"
    assert _inverted_to_text({}) == ""
    # Far positions are kept faithfully (gap spaces); the point is no crash.
    assert "far" in _inverted_to_text({"far": [99]})


@pytest.mark.asyncio
async def test_search_parses_records_with_abstracts() -> None:
    """Search results carrying inverted abstracts parse without crashing."""
    adapter = OpenAlexAdapter("test@example.com")
    adapter._client = lambda: _client(lambda request: httpx.Response(200, json=_works_payload()))  # type: ignore[method-assign]
    records = await adapter.search("synthetic data", limit=2)
    assert len(records) == 2
    first = records[0]
    assert first.source is SearchSource.OPENALEX
    assert first.doi == "10.1186/s40537-019-0197-0"
    assert first.abstract == "Deep learning survey"
    assert first.citation_count == 13235
    assert records[1].abstract is None


@pytest.mark.asyncio
async def test_search_raises_search_error() -> None:
    """HTTP failures surface as SearchError."""
    adapter = OpenAlexAdapter("test@example.com")
    adapter._client = lambda: _client(lambda request: httpx.Response(500))  # type: ignore[method-assign]
    with pytest.raises(SearchError):
        await adapter.search("synthetic data")


@pytest.mark.asyncio
async def test_search_strips_filter_punctuation() -> None:
    """Trailing ?/!/:/; in the query must not reach title.search (HTTP 400)."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["filter"] = str(request.url.params.get("filter", ""))
        return httpx.Response(200, json=_works_payload())

    adapter = OpenAlexAdapter("test@example.com")
    adapter._client = lambda: _client(handler)  # type: ignore[method-assign]
    await adapter.search("What is RAG?", limit=2)
    assert seen["filter"] == "title.search:What is RAG"

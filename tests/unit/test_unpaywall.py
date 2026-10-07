"""Unit tests: UnpaywallAdapter — env gating + DOI fetch (offline, stubbed)."""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.exceptions import ConfigurationError, SearchError
from knowledge_engine.infrastructure.search.unpaywall import UnpaywallAdapter, is_usable_email
from knowledge_engine.schemas.source import SearchSource

_ITEM = {
    "doi": "10.1038/nature12373",
    "title": "A paper",
    "is_oa": True,
    "published_date": "2013-05-30",
    "z_authors": [{"family": "Smith", "given": "Ada"}],
    "best_oa_location": {"url_for_pdf": "https://example.org/paper.pdf", "url": "https://example.org"},
}


def test_is_usable_email_rejects_placeholders() -> None:
    assert is_usable_email("researcher@university.edu")
    assert not is_usable_email("")
    assert not is_usable_email(None)
    assert not is_usable_email("no-at-sign")
    assert not is_usable_email("test@example.com")
    assert not is_usable_email("your-email@example.com")


def _adapter(handler, email="researcher@university.edu") -> UnpaywallAdapter:
    adapter = UnpaywallAdapter(email)
    adapter._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


async def _ok(request: httpx.Request) -> httpx.Response:
    assert request.url.params["email"] == "researcher@university.edu"
    return httpx.Response(200, json=_ITEM)


async def test_fetch_maps_oa_record() -> None:
    record = await _adapter(_ok).fetch("10.1038/nature12373")
    assert record is not None
    assert record.id == "unpaywall:10.1038/nature12373"
    assert record.source is SearchSource.UNPAYWALL
    assert record.pdf_url == "https://example.org/paper.pdf"
    assert record.authors == ["Smith, Ada"]
    assert record.year == 2013


async def test_fetch_without_email_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        await _adapter(_ok, email="").fetch("10.1038/nature12373")


async def test_fetch_returns_none_on_404() -> None:
    async def _missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    assert await _adapter(_missing).fetch("10.1000/missing") is None


async def test_fetch_raises_typed_error_on_http_failure() -> None:
    async def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    with pytest.raises(SearchError):
        await _adapter(_boom).fetch("10.1038/nature12373")


async def test_search_is_a_documented_noop() -> None:
    assert await _adapter(_ok).search("anything") == []

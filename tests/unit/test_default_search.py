"""Unit tests: default search composition (offline; no network)."""

from __future__ import annotations

from knowledge_engine.engine import _default_search, _default_web_chain
from knowledge_engine.infrastructure.search.cache import CachedSearchAdapter
from knowledge_engine.infrastructure.search.demux import DemuxSearchAdapter
from knowledge_engine.infrastructure.search.exa import ExaAdapter
from knowledge_engine.infrastructure.search.fallback import FallbackSearchAdapter
from knowledge_engine.infrastructure.search.tavily import TavilyAdapter


def test_web_chain_is_cached_fallback(tmp_path) -> None:
    chain = _default_web_chain(str(tmp_path))
    assert isinstance(chain, CachedSearchAdapter)
    assert isinstance(chain.inner, FallbackSearchAdapter)
    assert [p.name for p in chain.inner.providers] == ["duckduckgo"]


def test_web_chain_adds_keyed_providers_only_with_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    monkeypatch.setenv("EXA_API_KEY", "exa-y")
    chain = _default_web_chain(str(tmp_path))
    assert [p.name for p in chain.inner.providers] == ["duckduckgo", "tavily", "exa"]
    assert isinstance(chain.inner.providers[1], TavilyAdapter)
    assert isinstance(chain.inner.providers[2], ExaAdapter)


def test_default_search_drops_google_and_appends_one_web_chain(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    search = _default_search(str(tmp_path))
    assert isinstance(search, DemuxSearchAdapter)
    names = [getattr(source, "name", type(source).__name__) for source in search.sources]
    assert "google_cse" not in names
    assert names[-1].startswith("cached:")

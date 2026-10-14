"""Unit tests: ResearchEngine reads the SPEC cutover switches from env (issue #30).

The five switches were documentation-only; these pin the composition-root
wiring so a Render/Actions deployment can select cloud kinds with env vars
alone (an explicit kwarg still wins, keeping tests deterministic).
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_engine.engine import ResearchEngine

_KIND_KEYS = ("CHECKPOINTER_KIND", "STORE_KIND", "ARTIFACT_STORE_KIND", "BODY_STORE_KIND", "WIKI_STORE_KIND")


def _engine(**kwargs: Any) -> ResearchEngine:
    """Build an engine with inert adapters (no network, no cloud SDKs).

    Args:
        **kwargs: Forwarded to :class:`ResearchEngine`.

    Returns:
        ResearchEngine: Engine with injected provider/search/scrape/browser.
    """
    return ResearchEngine(
        provider=object(),
        search=object(),
        scrape=object(),
        browser=object(),
        discoverer=object(),
        **kwargs,
    )


def _stub_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the dep-requiring builders so construction stays offline.

    Args:
        monkeypatch: pytest monkeypatch.
    """
    monkeypatch.setattr(ResearchEngine, "_build_research_store", lambda self: object())
    monkeypatch.setattr(ResearchEngine, "_build_checkpointer", lambda self: object())
    monkeypatch.setattr(ResearchEngine, "_build_memory_store", lambda self: object())


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every switch from the environment.

    Args:
        monkeypatch: pytest monkeypatch.
    """
    for key in _KIND_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_switches_are_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Each switch resolves from its env var."""
    _clear(monkeypatch)
    _stub_builders(monkeypatch)
    monkeypatch.setenv("CHECKPOINTER_KIND", "postgres")
    monkeypatch.setenv("STORE_KIND", "postgres")
    monkeypatch.setenv("ARTIFACT_STORE_KIND", "supabase")
    monkeypatch.setenv("BODY_STORE_KIND", "supabase")
    monkeypatch.setenv("WIKI_STORE_KIND", "supabase")
    engine = _engine(store_root=str(tmp_path))
    assert engine.checkpointer_kind == "postgres"
    assert engine.store_kind == "postgres"
    assert engine.artifact_store_kind == "supabase"
    assert engine.body_store_kind == "supabase"
    assert engine.wiki_store_kind == "supabase"


def test_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """With no env and no kwargs, the portable defaults hold."""
    _clear(monkeypatch)
    _stub_builders(monkeypatch)
    engine = _engine(store_root=str(tmp_path))
    assert engine.checkpointer_kind == "file"
    assert engine.store_kind == "memory"
    assert engine.artifact_store_kind == "local"
    assert engine.body_store_kind is None
    assert engine.wiki_store_kind == "local"


def test_explicit_kwarg_wins_over_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """An explicit kwarg beats the ambient env (deterministic tests/embedders)."""
    _clear(monkeypatch)
    _stub_builders(monkeypatch)
    monkeypatch.setenv("WIKI_STORE_KIND", "supabase")
    engine = _engine(store_root=str(tmp_path), wiki_store_kind="local")
    assert engine.wiki_store_kind == "local"


def test_wiki_store_is_built_once_and_shared(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """``_wiki()`` builds the store once and returns the same object."""
    _clear(monkeypatch)
    _stub_builders(monkeypatch)
    calls = {"n": 0}

    def fake_build(self: ResearchEngine) -> object:
        calls["n"] += 1
        return object()

    monkeypatch.setattr(ResearchEngine, "_build_wiki_store", fake_build)
    engine = _engine(store_root=str(tmp_path))
    assert engine._wiki() is engine._wiki()
    assert calls["n"] == 1


async def test_close_closes_the_shared_wiki_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """``close()`` releases the shared wiki store and clears the cache."""
    _clear(monkeypatch)
    _stub_builders(monkeypatch)

    class FakeWiki:
        """Minimal closable wiki store."""

        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            """Record that close ran."""
            self.closed = True

    wiki = FakeWiki()
    monkeypatch.setattr(ResearchEngine, "_build_wiki_store", lambda self: wiki)
    engine = _engine(store_root=str(tmp_path))
    assert engine._wiki() is wiki
    await engine.close()
    assert wiki.closed is True
    assert engine._wiki_store is None


async def test_research_builds_the_wiki_store_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A full run builds the wiki store once (no nodeset+research double build)."""
    _clear(monkeypatch)
    from tests.conftest import FakeProvider, FakeScrape, FakeSearch

    calls = {"n": 0}
    original = ResearchEngine._build_wiki_store

    def counting(self: ResearchEngine) -> object:
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(ResearchEngine, "_build_wiki_store", counting)
    engine = ResearchEngine(
        store_root=str(tmp_path / "store"),
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=FakeProvider(),
    )
    await engine.research("multi-agent orchestration", workflow="qa", thread_id="t-wiki-once")
    assert calls["n"] == 1

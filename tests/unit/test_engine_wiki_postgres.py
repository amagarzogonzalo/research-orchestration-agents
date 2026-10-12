"""Unit tests: the engine wires ``wiki_store_kind='postgres'`` to PostgresWikiStore."""

from __future__ import annotations

import tempfile
from typing import Any

import pytest

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.infrastructure.wiki import PostgresVaultLinter, PostgresWikiStore


def _engine(*, kind: str) -> ResearchEngine:
    """Build a minimally-injected engine for wiring assertions.

    Args:
        kind: ``wiki_store_kind`` to configure.

    Returns:
        ResearchEngine: Engine with inert provider/search/scrape/browser.
    """
    return ResearchEngine(
        store_root=tempfile.mkdtemp(prefix=f"eng-{kind}-"),
        wiki_store_kind=kind,
        provider=object(),
        search=object(),
        scrape=object(),
        browser=object(),
        discoverer=object(),
    )


def test_postgres_kind_builds_postgres_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """``wiki_store_kind='postgres'`` dispatches to :meth:`PostgresWikiStore.from_env`.

    Args:
        monkeypatch: pytest monkeypatch.
    """
    built: dict[str, bool] = {}

    @classmethod
    def fake_from_env(cls: Any) -> Any:
        built["called"] = True
        return cls.__new__(cls)  # bypass DDL; wiring is what we assert

    monkeypatch.setattr(PostgresWikiStore, "from_env", fake_from_env)
    store = _engine(kind="postgres")._build_wiki_store()
    assert built.get("called") is True
    assert isinstance(store, PostgresWikiStore)


def test_unknown_kind_raises() -> None:
    """An unknown ``wiki_store_kind`` raises ConfigurationError.

    Raises:
        ConfigurationError: Always (the point of the test).
    """
    with pytest.raises(ConfigurationError):
        _engine(kind="bogus")._build_wiki_store()


def test_linter_is_exported() -> None:
    """The linter class is importable beside the store.

    Raises:
        AssertionError: When the class is missing.
    """
    assert PostgresVaultLinter is not None

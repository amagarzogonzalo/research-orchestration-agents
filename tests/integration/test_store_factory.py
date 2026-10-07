"""Integration tests: store factory (memory default; postgres opt-in)."""

from __future__ import annotations

import pytest

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.orchestration.store_factory import build_store


def test_memory_default() -> None:
    from langgraph.store.memory import InMemoryStore

    assert isinstance(build_store(), InMemoryStore)


def test_postgres_needs_dsn(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError):
        build_store("postgres")


def test_postgres_returns_context_manager() -> None:
    pytest.importorskip("langgraph.store.postgres.aio")
    cm = build_store("postgres", conn_string="postgresql://user:pass@localhost:5432/postgres?sslmode=disable")
    assert hasattr(cm, "__aenter__") and hasattr(cm, "__aexit__")


def test_unknown_kind_raises() -> None:
    with pytest.raises(ConfigurationError):
        build_store("redis")

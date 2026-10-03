"""Integration tests: checkpointer factory (memory/file default; postgres opt-in).

Default stays files-only: the durable checkpointer is the JSON-file one under
``research_store/checkpoints``. ``postgres`` (Supabase) is the opt-in cloud kind
and needs the ``store`` extra + ``DATABASE_URL``.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.orchestration.checkpoint import build_checkpointer
from knowledge_engine.store.file_checkpointer import FileCheckpointer


def test_memory_default() -> None:
    assert isinstance(build_checkpointer(), InMemorySaver)


def test_memory_explicit() -> None:
    assert isinstance(build_checkpointer("memory"), InMemorySaver)


def test_file_kind_returns_file_checkpointer(tmp_path) -> None:
    cp = build_checkpointer("file", root=str(tmp_path / "ckpt"))
    assert isinstance(cp, FileCheckpointer)


def test_postgres_kind_needs_dsn_and_extra(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError):
        build_checkpointer("postgres")


def test_postgres_kind_returns_context_manager() -> None:
    pytest.importorskip("langgraph.checkpoint.postgres.aio")
    cm = build_checkpointer("postgres", conn_string="postgresql://user:pass@localhost:5432/postgres?sslmode=disable")
    assert hasattr(cm, "__aenter__") and hasattr(cm, "__aexit__")


def test_legacy_db_kinds_raise_cleanly() -> None:
    # SQLite/Redis/Kafka stay out of scope; postgres is the only DB kind.
    for kind in ("sqlite", "redis", "kafka"):
        with pytest.raises(ConfigurationError):
            build_checkpointer(kind)

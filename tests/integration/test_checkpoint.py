"""Integration tests: checkpointer factory (memory default; file → FileCheckpointer).

Files-only philosophy: no SQLite/Postgres — the durable checkpointer is the
JSON-file one under ``research_store/checkpoints``.
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


def test_legacy_db_kinds_raise_cleanly() -> None:
    # SQLite/Postgres/Redis are out of scope for the files-only library.
    for kind in ("sqlite", "postgres", "redis", "kafka"):
        with pytest.raises(ConfigurationError):
            build_checkpointer(kind)

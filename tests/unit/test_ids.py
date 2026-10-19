"""Unit tests: safe thread ids cannot escape store/checkpoint roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.schemas.ids import validate_thread_id
from knowledge_engine.store.file_checkpointer import FileCheckpointer
from knowledge_engine.store.research_store import ResearchStore


@pytest.mark.parametrize(
    "value",
    ["", "..", ".hidden", "../escape", "a/b", r"a\b", "x" * 129, "safe id"],
)
def test_rejects_unsafe_thread_ids(value: str) -> None:
    """Traversal, separators, blanks, and overlong ids are refused."""
    with pytest.raises(ValueError):
        validate_thread_id(value)


@pytest.mark.parametrize("value", ["t-1", "thread_1", "e2e-hitl", "a.b", "A" * 128])
def test_accepts_safe_thread_ids(value: str) -> None:
    """Normal derived/API thread ids remain valid."""
    assert validate_thread_id(value) == value


def test_research_store_rejects_escape_before_mkdir(tmp_path: Path) -> None:
    """The store root guard fires before a directory is created outside it."""
    escape = tmp_path.parent / "escape-marker"
    with pytest.raises(ValueError):
        ResearchStore(tmp_path).run_dir(f"../{escape.name}")
    assert not escape.exists()


def test_file_checkpointer_rejects_escape_before_mkdir(tmp_path: Path) -> None:
    """Checkpoints cannot be written outside the checkpoint root either."""
    escape = tmp_path.parent / "ckpt-escape-marker"
    with pytest.raises(ValueError):
        FileCheckpointer(str(tmp_path))._thread_dir(f"../{escape.name}")
    assert not escape.exists()

"""Checkpointer factory — the ONLY persistence abstraction (ADR 0008 §3).

Files-only philosophy: ``memory`` is the in-process default; the durable,
portable option is the file-based :class:`FileCheckpointer`. No SQLite, no
Postgres, no Redis — a folder ``research_store/checkpoints`` moves with the
data.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.store.file_checkpointer import FileCheckpointer


def build_checkpointer(kind: str = "memory", **kwargs: Any) -> BaseCheckpointSaver[Any]:
    """Return the checkpointer for ``kind`` (``memory`` or ``file``).

    Args:
        kind: ``memory`` (in-process, default) or ``file`` (durable, portable).
        **kwargs: Passed to the chosen checkpointer (e.g. ``root`` for file).

    Returns:
        BaseCheckpointSaver[Any]: A LangGraph checkpointer.

    Raises:
        ConfigurationError: For unknown kinds (SQLite/Postgres are out of scope).
    """
    if kind == "memory":
        return InMemorySaver()
    if kind == "file":
        return FileCheckpointer(root=kwargs.get("root", "research_store/checkpoints"))
    raise ConfigurationError(
        f"unknown checkpointer kind: {kind!r} (use 'memory' or 'file' — the library is files-only)"
    )


__all__ = ["build_checkpointer"]

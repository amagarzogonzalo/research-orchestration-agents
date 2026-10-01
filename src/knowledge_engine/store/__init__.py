"""Files-only persistence for the research library (portable, zero-server).

``research_store/`` (configurable root) holds every run's artifacts as plain
files: ``metadata.json``, ``events.jsonl``, ``sources.json``, ``article.md``,
and ``checkpoints/`` (LangGraph checkpoints as JSON). Copy the folder anywhere —
no SQLite, no Postgres, no Redis, no vector DB.
"""

from __future__ import annotations

from knowledge_engine.store.file_checkpointer import FileCheckpointer
from knowledge_engine.store.research_store import ResearchStore

__all__ = ["FileCheckpointer", "ResearchStore"]

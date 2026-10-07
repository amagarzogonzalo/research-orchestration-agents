"""Studio entrypoint — import-safe compiled graphs for LangGraph Studio.

No keys, no network, no Supabase at import: memory persistence only. Heavy
lifting (providers, search) constructs adapters lazily without I/O.
"""

from __future__ import annotations

from knowledge_engine.engine import ResearchEngine

_engine = ResearchEngine(checkpointer_kind="memory", store_kind="memory")

graph_deep = _engine.compiled_graph("deep")
"""Recursive deep-research workflow (intent → plan → branches → rubric → judge)."""

graph_research = _engine.compiled_graph("research")
"""Single-pass research workflow (ingest → relevance → bodies → judge → review)."""

__all__ = ["graph_deep", "graph_research"]

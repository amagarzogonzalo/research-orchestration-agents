"""render node: materialize the final knowledge artifact (Action step).

Builds a schema-aligned :class:`~knowledge_engine.schemas.artifact.KnowledgeArtifact`
(YAML frontmatter + tldr) and persists it through an injected ``save`` callable
(defaults to a local ``knowledge_base/<thread>/article.md`` write so a run is
verifiable offline). Returns ``output_path`` + ``status=done``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.extraction import GraphStatus
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)


class RenderNode(WorkflowNode):
    """Persist a :class:`KnowledgeArtifact`; inject ``save`` for testability."""

    __slots__ = ("_root", "_save")

    def __init__(self, root: str = "knowledge_base", save: Any | None = None) -> None:
        self._root = root
        self._save = save  # async (KnowledgeArtifact) -> str (output_path)

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        notes = "\n\n".join(state.get("notes", []))
        provenance = state.get("filtered") or state.get("sources", [])
        title = _title_from(state.get("query", ""))
        artifact = KnowledgeArtifact(
            id=f"{state['thread_id']}",
            title=title,
            provenance=provenance,
            body_markdown=notes,
            citations=[s.doi or s.id for s in provenance],
            tldr_llm=_tldr(notes),
            frontmatter_yaml=_frontmatter(state, title, provenance),
            generated_at=datetime.now(UTC).isoformat(),
        )
        if self._save is not None:
            output_path = await self._save(artifact)
        else:
            output_path = await _default_save(self._root, artifact)
        return {"output_path": output_path, "status": GraphStatus.DONE.value}


def _frontmatter(state: WorkflowState, title: str, provenance: list[SourceRecord]) -> str:
    meta = {
        "@id": state.get("thread_id"),
        "type": "KnowledgeBaseArticle",
        "title": title,
        "provenance": [{"id": s.id, "doi": s.doi, "title": s.title} for s in provenance],
        "citations": [s.doi or s.id for s in provenance],
        "tldr_llm": _tldr("\n\n".join(state.get("notes", []))),
    }
    dumped = yaml.safe_dump(meta, sort_keys=False)
    return dumped or ""


def _tldr(notes: str) -> str:
    return (notes.replace("\n", " ").strip() or "")[:200]


def _title_from(query: str) -> str:
    return query.strip().strip("?")[:80] or "Research Notes"


async def _default_save(root: str, artifact: KnowledgeArtifact) -> str:
    path = Path(root) / artifact.id / "article.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}\n", encoding="utf-8")
    return str(path)


__all__ = ["RenderNode"]

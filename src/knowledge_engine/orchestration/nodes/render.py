"""render node: materialize the final knowledge artifact (Action step).

Builds a schema-aligned :class:`~knowledge_engine.schemas.artifact.KnowledgeArtifact`
(YAML frontmatter + tldr) and persists it through an injected ``save`` callable
(defaults to a local ``knowledge_base/<thread>/article.md`` write so a run is
verifiable offline). Returns ``output_path`` + ``status=done``.
"""

from __future__ import annotations

import logging
import re
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
        """Create a RenderNode.

        Args:
            root: Store root directory.
            save: Optional save callable.
        """
        self._root = root
        self._save = save  # async (KnowledgeArtifact) -> str (output_path)

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        notes = _complete_footnotes(
            "\n\n".join(state.get("notes", [])),
            state.get("filtered") or state.get("sources", []),
        )
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
    """Frontmatter.

    Args:
        state: Current workflow state.
        title: Title text.
        provenance: Provenance records.

    Returns:
        str: Result text.

    """
    meta = {
        "@id": state.get("thread_id"),
        "type": "KnowledgeBaseArticle",
        "title": title,
        "provenance": [{"id": s.id, "doi": s.doi, "title": s.title, "venue": s.venue} for s in provenance],
        "citations": [s.doi or s.id for s in provenance],
        "tldr_llm": _tldr("\n\n".join(state.get("notes", []))),
    }
    dumped = yaml.safe_dump(meta, sort_keys=False)
    return dumped or ""


def _tldr(notes: str) -> str:
    """Tldr.

    Args:
        notes: Review notes.

    Returns:
        str: Result text.

    """
    return (notes.replace("\n", " ").strip() or "")[:200]


_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_.:-]+)\]")
_FOOTNOTE_DEF = re.compile(r"(?m)^\[\^([A-Za-z0-9_.:-]+)\]:")


def _complete_footnotes(notes: str, provenance: list[SourceRecord]) -> str:
    """Append missing footnote definitions from provenance.

    Models cite [^<source-id>] but forget some definitions; every dangling
    marker is completed from the source title so references always resolve.
    Markers for unknown IDs are left untouched.

    Args:
        notes: Synthesized notes.
        provenance: Provenance records.

    Returns:
        str: Notes with complete footnote definitions.
    """
    titles = {s.id: (s.title or s.id) for s in provenance}
    missing = sorted(set(_FOOTNOTE_REF.findall(notes)) - set(_FOOTNOTE_DEF.findall(notes)))
    additions = []
    for marker in missing:
        sid = _resolve_marker(marker, titles)
        if sid is not None:
            additions.append(f"[^{marker}]: {titles[sid]}")
    if not additions:
        return notes
    return notes.rstrip() + "\n\n" + "\n".join(additions) + "\n"


def _resolve_marker(marker: str, titles: dict[str, str]) -> str | None:
    """Resolve a footnote marker to a provenance source id.

    Models shorten IDs (``arx-2412-11919`` for ``huggingface_papers:2412.11919``);
    match exact, ``/``- or ``:``-suffixed, then normalized-substring IDs.

    Args:
        marker: Footnote marker text.
        titles: Source id to title mapping.

    Returns:
        str | None: Matching source id, or None when unresolvable.
    """
    if marker in titles:
        return marker
    low = marker.lower()
    for sid in titles:
        slowed = sid.lower()
        if slowed.endswith("/" + low) or slowed.endswith(":" + low):
            return sid
    norm = re.sub(r"[^a-z0-9]", "", low)
    for sid in titles:
        # Match a shortened marker against the bare id (after the last : or /):
        # "arx-2412-11919" resolves "huggingface_papers:2412.11919".
        bare = re.sub(r"[^a-z0-9]", "", re.split(r"[:/]", sid)[-1].lower())
        if len(bare) >= 8 and norm.endswith(bare):
            return sid
    return None


def _title_from(query: str) -> str:
    """Title from.

    Args:
        query: Scholarly query string.

    Returns:
        str: Result text.
    """
    return query.strip().strip("?")[:80] or "Research Notes"


async def _default_save(root: str, artifact: KnowledgeArtifact) -> str:
    """Default save.

    Args:
        root: Store root directory.
        artifact: Final knowledge artifact.

    Returns:
        str: Result text.
    """
    path = Path(root) / artifact.id / "article.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}\n", encoding="utf-8")
    return str(path)


__all__ = ["RenderNode"]

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

from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState, merge_sources
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
        # Research path keeps filtered-wins semantics; deep branches contribute
        # their actually-used sources through the add-reducer channel.
        base = state.get("filtered") or state.get("sources", [])
        provenance = merge_sources(base, state.get("branch_sources", []))
        notes = _normalize_citations(
            "\n\n".join(state.get("notes", [])),
            provenance,
        )
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


_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_.:/-]+)\]")
_FOOTNOTE_DEF = re.compile(r"(?m)^\[\^([A-Za-z0-9_.:/-]+)\]:")


def _normalize_citations(notes: str, provenance: list[SourceRecord]) -> str:
    """Rewrite plain [known-id] citations to footnote markers, then complete defs.

    Models alternate between ``[^id]`` and bare ``[id]`` styles run to run;
    normalizing first makes the published reference format deterministic.

    Args:
        notes: Synthesized notes.
        provenance: Provenance records.

    Returns:
        str: Notes with footnote markers and complete definitions.
    """
    known: dict[str, str] = {}
    for record in provenance:
        known[record.id] = record.id
        if record.doi:
            known[record.doi] = record.id

    def _rewrite(match: re.Match[str]) -> str:
        sid = known.get(match.group(1))
        return f"[^{sid}]" if sid is not None else match.group(0)

    notes = re.sub(r"(?<!\^)\[([A-Za-z0-9_.:/-]+)\]", _rewrite, notes)
    # Split multi-cite brackets ([^a, b] and [^a, ^b] -> [^a] [^b]) so every
    # marker is individually resolvable by the footnote completion below.
    notes = re.sub(
        r"\[\^([A-Za-z0-9_.:/-]+(?:\s*,\s*\^?[A-Za-z0-9_.:/-]+)+)\]",
        lambda m: " ".join(f"[^{part.strip().lstrip('^')}]" for part in m.group(1).split(",")),
        notes,
    )
    return _complete_footnotes(notes, provenance)


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
            if sid != marker:
                # Canonicalize merged-away duplicate IDs to the surviving
                # provenance ID so every marker matches its definition.
                notes = re.sub(r"\[\^" + re.escape(marker) + r"\]", f"[^{sid}]", notes)
            additions.append(f"[^{sid}]: {_full_citation(provenance, sid)}")
    if not additions:
        return notes
    return notes.rstrip() + "\n\n" + "\n".join(dict.fromkeys(additions)) + "\n"


def _full_citation(provenance: list[SourceRecord], sid: str) -> str:
    """Render a full reference line for a footnote definition.

    Args:
        provenance: Provenance records.
        sid: Source id.

    Returns:
        str: Authored citation (title, authors, venue, year, doi/url).
    """
    rec = next((s for s in provenance if s.id == sid), None)
    if rec is None:
        return sid
    parts = [rec.title or sid]
    if rec.authors:
        parts.append(", ".join(rec.authors[:4]))
    meta = ", ".join(p for p in (rec.venue, str(rec.year or "")) if p)
    if meta:
        parts.append(f"({meta})")
    if rec.pdf_url:
        parts.append(rec.pdf_url)
    elif rec.doi:
        parts.append(f"https://doi.org/{rec.doi}")
    else:
        # General-web records carry no DOI/PDF; fall back to the URL embedded
        # in the canonical id so the footnote still resolves to the source.
        url = _web_url(rec)
        if url:
            parts.append(url)
    return " — ".join(parts)


def _web_url(rec: SourceRecord) -> str | None:
    """Extract the target URL from a general-web record's canonical id.

    Args:
        rec: Source record (tavily/exa/duckduckgo/wikipedia).

    Returns:
        str | None: Resolvable URL, or None for scholarly-only records.
    """
    if rec.id.startswith(("tavily:", "exa:", "duckduckgo:")):
        url = rec.id.partition(":")[2]
        return url if url.startswith("http") else None
    if rec.id.startswith("wikipedia:"):
        pageid = rec.id.partition(":")[2]
        return f"https://en.wikipedia.org/wiki/?curid={pageid}" if pageid else None
    return None


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
    # Collapse mechanically doubled prefixes ("10.1/10.1/x" -> "10.1/x") and
    # match all subsequent forms against the deduped marker.
    marker = re.sub(r"([^/]+/)\1", r"\1", marker)
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
    # Match URL-embedded IDs from merged-away duplicates: a general-web record
    # like "duckduckgo:https://arxiv.org/html/2504.06214v1" shares its paper
    # with the surviving "arxiv:2504.06214" but never matches textually.
    # Compare version-stripped alphanumeric tails instead.
    marker_tail = re.sub(r"v\d+$", "", re.split(r"[:/]", low)[-1])
    marker_tail = re.sub(r"[^a-z0-9]", "", marker_tail)
    if len(marker_tail) >= 8:
        for sid in titles:
            sid_tail = re.sub(r"v\d+$", "", re.split(r"[:/]", sid.lower())[-1])
            sid_tail = re.sub(r"[^a-z0-9]", "", sid_tail)
            if len(sid_tail) < 8:
                continue
            if marker_tail == sid_tail or marker_tail.endswith(sid_tail) or sid_tail.endswith(marker_tail):
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

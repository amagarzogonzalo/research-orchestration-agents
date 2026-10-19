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
from knowledge_engine.schemas.source import SearchSource, SourceRecord

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
        # Judge-loop iterations append to ``notes``; publish only the final
        # synthesis (joining them re-published earlier drafts in the artifact).
        drafts = [note for note in state.get("notes", []) if note and note.strip()]
        notes = _normalize_citations(drafts[-1] if drafts else "", provenance)
        title = _title_from(state.get("query", ""))
        tldr = _tldr(notes)
        cited = set(_cited_ids(notes, provenance))
        citations = [s.doi or s.id for s in provenance if s.id in cited]
        artifact = KnowledgeArtifact(
            id=f"{state['thread_id']}",
            title=title,
            provenance=provenance,
            body_markdown=notes,
            citations=citations,
            tldr_llm=tldr,
            frontmatter_yaml=_frontmatter(state, title, provenance, citations, tldr),
            generated_at=datetime.now(UTC).isoformat(),
        )
        if self._save is not None:
            output_path = await self._save(artifact)
        else:
            output_path = await _default_save(self._root, artifact)
        return {"output_path": output_path, "status": GraphStatus.DONE.value}


def _frontmatter(
    state: WorkflowState, title: str, provenance: list[SourceRecord], citations: list[str], tldr: str
) -> str:
    """Frontmatter.

    Args:
        state: Current workflow state.
        title: Title text.
        provenance: Provenance records.
        citations: Cited source locators (DOI or id).
        tldr: Summary text.

    Returns:
        str: Result text.

    """
    meta = {
        "@id": state.get("thread_id"),
        "type": "KnowledgeBaseArticle",
        "title": title,
        "provenance": [{"id": s.id, "doi": s.doi, "title": s.title, "venue": s.venue} for s in provenance],
        "citations": citations,
        "tldr_llm": tldr,
    }
    dumped = yaml.safe_dump(meta, sort_keys=False)
    return dumped or ""


def _tldr(notes: str) -> str:
    """First prose of the article, without headings and cut at a word boundary.

    Args:
        notes: Normalized article markdown.

    Returns:
        str: Summary text.
    """
    lines: list[str] = []
    for line in notes.splitlines():
        text = line.strip()
        if not text or text.startswith(("#", "---", "```", "[^")):
            continue
        lines.append(text)
        if len(" ".join(lines)) >= 240:
            break
    collapsed = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(collapsed) <= 200:
        return collapsed
    cut = collapsed[:200]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:")


_MARKER_TEXT = r"[^\[\]\n]+"
_FOOTNOTE_REF = re.compile(r"\[\^(" + _MARKER_TEXT + r")\]")
_FOOTNOTE_DEF_BLOCK = re.compile(r"(?m)^\[\^[^\[\]\n]+\]:[^\n]*(?:\n[ \t]+\S[^\n]*)*\n?")
_BARE_CITE = re.compile(r"(?<![!\^])\[([^\[\]\n]+)\](?![\(:])")
_DOUBLED_PREFIX = re.compile(r"((?:[a-z_]+):https?://)(?:\1)+")
_DOUBLED_SCHEME = re.compile(r"(https?://)(?:\1)+")
_BROKEN_FRAGMENT = re.compile(r"(?m)^\[\^[^\]\n]*(?=\n|$)\n?")
_CITATION_SCHEMES = frozenset(source.value for source in SearchSource) | {"doi", "osf", "pmc"}
_ID_DOI = re.compile(r"^(?:doi:)?10\.\d{4,9}/")


def _normalize_citations(notes: str, provenance: list[SourceRecord]) -> str:
    """Rewrite citations to canonical markers and rebuild the bibliography.

    The model alternates between ``[^id]`` and bare ``[id]`` styles, doubles
    URL prefixes, comma-joins markers, truncates definitions mid-line, and
    leaves definitions for sources it never cites. This normalizes every
    marker to a resolvable ``[^source-id]`` form, drops markers for sources
    that were never retrieved, and regenerates the bibliography from
    provenance for exactly the cited ids.

    Args:
        notes: Synthesized notes.
        provenance: Provenance records.

    Returns:
        str: Notes with canonical footnote markers and complete definitions.
    """
    titles = {s.id: (s.title or s.id) for s in provenance}
    known = _known_ids(provenance)
    body = _drop_broken_marker_fragments(notes)
    body = _collapse_doubled_prefixes(body)
    body = _rewrite_bare_citations(body, known, titles)
    body = _rewrite_multi_refs(body, known, titles)
    body = _rewrite_refs(body, known, titles)
    body = re.sub(r"(?<=\S)[ \t]{2,}", " ", body)
    body = re.sub(r"[ \t]+([.,;:)])", r"\1", body)
    return _rebuild_bibliography(body, provenance, titles)


def _known_ids(provenance: list[SourceRecord]) -> dict[str, str]:
    """Build the accepted-marker map (canonical ids plus DOIs).

    Args:
        provenance: Provenance records.

    Returns:
        dict[str, str]: Marker text to canonical source id.
    """
    known: dict[str, str] = {}
    for record in provenance:
        known[record.id] = record.id
        if record.doi:
            known[record.doi] = record.id
    return known


def _collapse_doubled_prefixes(text: str) -> str:
    """Collapse mechanically doubled provider prefixes and URL schemes.

    Args:
        text: Article text.

    Returns:
        str: Text with doubled ``tavily:https://tavily:https://`` and
            ``https://https://`` runs collapsed to one copy.
    """
    text = _DOUBLED_PREFIX.sub(r"\1", text)
    return _DOUBLED_SCHEME.sub(r"\1", text)


def _drop_broken_marker_fragments(text: str) -> str:
    """Remove unclosed footnote fragments left by truncated model output.

    The model sometimes emits a marker whose closing bracket never arrives
    (``[^crossref:10.21203/rs.3.rs`` alone on a line). Footnote repair needs a
    closed form to act on, so these fragments are dropped here.

    Args:
        text: Article text.

    Returns:
        str: Text without unterminated ``[^`` fragments that occupy a line.
    """
    return _BROKEN_FRAGMENT.sub("", text)


def _looks_like_locator(text: str) -> bool:
    """Whether marker text is shaped like a source locator, not prose.

    Args:
        text: Candidate bracket part (a leading caret is ignored).

    Returns:
        bool: True for URLs, DOIs, and ``scheme:rest`` provider ids.
    """
    low = text.strip().lstrip("^").strip().lower()
    if not low:
        return False
    if low.startswith(("http://", "https://")) or _ID_DOI.match(low):
        return True
    scheme, sep, rest = low.partition(":")
    if not sep:
        return False
    if scheme in _CITATION_SCHEMES:
        return True
    return "/" in rest and any(char.isdigit() for char in rest)


def _canonical_marker(marker: str, known: dict[str, str], titles: dict[str, str]) -> str | None:
    """Resolve one marker text to a provenance source id.

    Args:
        marker: Marker text (a leading caret is ignored).
        known: Marker-to-id map from :func:`_known_ids`.
        titles: Source id to title mapping.

    Returns:
        str | None: Canonical source id, or None when unresolvable.
    """
    text = marker.strip().lstrip("^").strip()
    if not text:
        return None
    if text in known:
        return known[text]
    return _resolve_marker(text, titles)


def _rewrite_bare_citations(text: str, known: dict[str, str], titles: dict[str, str]) -> str:
    """Convert bare ``[id]`` / ``[id1, id2]`` citations to footnote markers.

    Multi-part brackets are split per source: resolvable parts become markers,
    unresolvable parts shaped like locators (URLs, DOIs, provider ids) are
    dropped, and prose parts are kept as text. Brackets whose parts are all
    unresolvable prose are left untouched so bracketed text is never mangled.

    Args:
        text: Article text.
        known: Marker-to-id map.
        titles: Source id to title mapping.

    Returns:
        str: Text with resolvable bare citations rewritten.
    """

    def _sub(match: re.Match[str]) -> str:
        parts = [part.strip().lstrip("^").strip() for part in match.group(1).split(",")]
        resolved = [_canonical_marker(part, known, titles) for part in parts]
        if "," not in match.group(1):
            sid = resolved[0]
            if sid is not None:
                return f"[^{sid}]"
            return match.group(0)
        pieces: list[str] = []
        changed = False
        for part, sid in zip(parts, resolved, strict=True):
            if sid is not None:
                pieces.append(f"[^{sid}]")
                changed = True
            elif _looks_like_locator(part):
                changed = True
            else:
                pieces.append(part)
        if not changed:
            return match.group(0)
        return " ".join(pieces)

    return _BARE_CITE.sub(_sub, text)


def _rewrite_multi_refs(text: str, known: dict[str, str], titles: dict[str, str]) -> str:
    """Split comma-joined footnote markers into individual markers.

    Resolvable parts are kept even when a sibling part is unknown, so one
    bad id never discards the good citations beside it.

    Args:
        text: Article text.
        known: Marker-to-id map.
        titles: Source id to title mapping.

    Returns:
        str: Text with multi-cite markers split into single markers.
    """

    def _sub(match: re.Match[str]) -> str:
        inner = match.group(1)
        if "," not in inner:
            return match.group(0)
        parts = [part.strip().lstrip("^").strip() for part in inner.split(",")]
        kept = [sid for sid in (_canonical_marker(part, known, titles) for part in parts) if sid is not None]
        if not kept:
            return match.group(0)
        return " ".join(f"[^{sid}]" for sid in kept)

    return _FOOTNOTE_REF.sub(_sub, text)


def _rewrite_refs(text: str, known: dict[str, str], titles: dict[str, str]) -> str:
    """Canonicalize every footnote marker and drop unresolvable ones.

    Args:
        text: Article text.
        known: Marker-to-id map.
        titles: Source id to title mapping.

    Returns:
        str: Text where every remaining marker points at a provenance id.
    """

    def _sub(match: re.Match[str]) -> str:
        sid = _canonical_marker(match.group(1), known, titles)
        return f"[^{sid}]" if sid is not None else ""

    return _FOOTNOTE_REF.sub(_sub, text)


def _rebuild_bibliography(body: str, provenance: list[SourceRecord], titles: dict[str, str]) -> str:
    """Replace model-authored definitions with a provenance-backed bibliography.

    Model definitions are removed first, so truncated, doubled, or unreferenced
    lines never reach the artifact; definitions are then regenerated for every
    marker that survived normalization.

    Args:
        body: Article text (markers already canonical).
        provenance: Provenance records.
        titles: Source id to title mapping.

    Returns:
        str: Article with a complete, deterministic bibliography.
    """
    body = _FOOTNOTE_DEF_BLOCK.sub("", body).rstrip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    cited = _referenced_ids(body, titles)
    if not cited:
        return body
    lines = [f"[^{sid}]: {_full_citation(provenance, sid)}" for sid in cited]
    return body + "\n\n" + "\n".join(lines) + "\n"


def _referenced_ids(body: str, titles: dict[str, str]) -> list[str]:
    """Ordered source ids referenced by footnote markers in ``body``.

    Args:
        body: Article text.
        titles: Source id to title mapping.

    Returns:
        list[str]: Source ids in first-use order, deduplicated.
    """
    seen: list[str] = []
    for marker in _FOOTNOTE_REF.findall(body):
        sid = _resolve_marker(marker, titles)
        if sid is not None and sid not in seen:
            seen.append(sid)
    return seen


def _cited_ids(notes: str, provenance: list[SourceRecord]) -> list[str]:
    """Source ids cited by the normalized notes, in first-use order.

    Args:
        notes: Normalized article text.
        provenance: Provenance records.

    Returns:
        list[str]: Cited source ids.
    """
    titles = {s.id: (s.title or s.id) for s in provenance}
    return _referenced_ids(notes, titles)


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
        url = _web_url(rec)
        if url:
            parts.append(url)
    return " — ".join(parts)


def _web_url(rec: SourceRecord) -> str | None:
    """Extract the target URL from a general-web record's canonical id.

    General-web records (tavily/exa/duckduckgo/wikipedia) carry no DOI/PDF;
    the footnote still needs a resolvable locator, so the URL embedded in the
    canonical id is used.

    Args:
        rec: Source record.

    Returns:
        str | None: Resolvable URL, or None for scholarly-only records.
    """
    if rec.id.startswith(("tavily:", "exa:", "duckduckgo:", "serper:")):
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
    return query.strip()[:80].strip() or "Research Notes"


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

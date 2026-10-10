"""Wiki layer schemas (ADR wiki-loop0): hits, ingest verdicts, lint reports.

The wiki layer is deterministic in Loop 0: no LLM calls, no embeddings. Hits
come from index/title scoring over ``<wiki_root>``; ingest files the rendered
article as a source page with a SHA256 cache so unchanged sources never
re-write. Every type is Pydantic and cross-port safe.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WikiVerdict(StrEnum):
    """Ingest verdict applied when a source is filed into the wiki.

    Attributes:
        NEW: First filing of this slug; a page is created.
        UPDATE: The slug exists; the page is merged/refreshed in place.
        DISPUTED: Reserved for LLM conflict adjudication (Loop 1+); Loop 0
            never emits it.
        NO_MATERIAL: The content hash is unchanged (or empty); only the log
            is touched, no page writes.
    """

    NEW = "new"
    UPDATE = "update"
    DISPUTED = "disputed"
    NO_MATERIAL = "no_material"


class WikiHit(BaseModel):
    """One wiki page matched by :meth:`WikiQueryPort.search_wiki`.

    Attributes:
        model_config: Forbid extra keys so hits stay schema-stable.
        slug: Page slug (relative path without extension, kebab-case).
        title: Page title (from frontmatter or filename).
        path: Absolute path to the page markdown.
        score: Relevance score (term overlap; higher is better).
        summary: One-line summary from the index entry, when present.
        query_sha256: SHA256 of the query the page was filed for (empty for
            legacy pages). Routing-on-hit requires an exact match to this
            value so near-miss pages never replace the live pipeline.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    path: str
    score: float = 0.0
    summary: str = ""
    query_sha256: str = ""


class IngestResult(BaseModel):
    """Outcome of filing one source into the wiki.

    Attributes:
        model_config: Forbid extra keys.
        verdict: The applied verdict (new/update/no_material; disputed is L1+).
        page_path: Path of the written (or existing) source page; None when
            nothing was written.
        pages_touched: Number of wiki files written or updated this ingest.
        content_sha256: SHA256 of the ingested markdown (cache key).
    """

    model_config = ConfigDict(extra="forbid")

    verdict: WikiVerdict
    page_path: str | None = None
    pages_touched: int = 0
    content_sha256: str = ""


class LintReport(BaseModel):
    """Deterministic vault health report (no LLM; the LLM pass is Loop 1+).

    Attributes:
        model_config: Forbid extra keys.
        root: Vault root that was scanned.
        pages: Number of markdown pages found under the wiki dirs.
        missing_frontmatter: Pages without the required frontmatter keys.
        broken_links: ``[[wikilinks]]`` pointing at pages that do not exist.
        orphans: Pages with no inbound links (excluding index/log/overview).
        thin_pages: Pages under ``_THIN_CHARS`` of body text.
        index_drift: Pages present on disk but missing from ``index.md``.
    """

    model_config = ConfigDict(extra="forbid")

    root: str
    pages: int = 0
    missing_frontmatter: list[str] = Field(default_factory=list)
    broken_links: list[str] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    thin_pages: list[str] = Field(default_factory=list)
    index_drift: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when the report has no findings.

        Returns:
            bool: True on success.
        """
        return not (
            self.missing_frontmatter or self.broken_links or self.orphans or self.thin_pages or self.index_drift
        )


class WikiExtraction(BaseModel):
    """Two-step ingest output: concepts + entities distilled from one source.

    The LLM proposes; validation disposes. Placeholder names (``n/a``,
    ``unknown``, ``todo``, ...) are rejected at the schema so a transient
    provider outage can never poison the wiki with stand-in structure
    (fail-closed, per the Karpathy thread's deterministic write gates).

    Attributes:
        model_config: Forbid extra keys.
        concepts: Concept names worth their own wiki pages (>= 1 char).
        entities: Entity names (people/orgs/tools/repos) worth pages.
        summary: One-paragraph synthesis of the source.
        contradictions: Claims in the source that tension existing pages.
    """

    model_config = ConfigDict(extra="forbid")

    concepts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    summary: str = ""
    contradictions: list[str] = Field(default_factory=list)

    @field_validator("concepts", "entities", mode="before")
    @classmethod
    def _drop_placeholders(cls, names: Any) -> Any:
        """Strip whitespace and refuse placeholder names.

        Args:
            names: Raw name list from the provider.

        Returns:
            Any: Cleaned list.

        Raises:
            ValueError: When a name is a placeholder (fail-closed).
        """
        if not isinstance(names, list):
            return names
        cleaned = []
        for name in names:
            text = str(name).strip()
            if not text:
                continue
            if text.lower() in _PLACEHOLDERS:
                raise ValueError(f"placeholder name rejected by wiki gate: {text!r}")
            cleaned.append(text)
        return cleaned


class WikiThesis(BaseModel):
    """Thesis-mode verdict: evidence for + against one claim, then a verdict.

    Attributes:
        model_config: Forbid extra keys.
        claim: The investigated claim.
        for_claims: Evidence lines supporting the claim.
        against_claims: Evidence lines challenging the claim.
        verdict: The weighed conclusion (short paragraph).
        sources: Wiki page slugs the evidence was drawn from.
    """

    model_config = ConfigDict(extra="forbid")

    claim: str
    for_claims: list[str] = Field(default_factory=list)
    against_claims: list[str] = Field(default_factory=list)
    verdict: str = ""
    sources: list[str] = Field(default_factory=list)


_PLACEHOLDERS = {"n/a", "na", "none", "unknown", "unnamed", "placeholder", "todo", "tbd"}


class DeepLintReport(BaseModel):
    """LLM-assisted lint: contradictions, stale claims and knowledge gaps.

    Complements the deterministic :class:`LintReport` with what only a model
    can see (tensions between pages, claims newer sources supersede, concepts
    mentioned but lacking a page). The provider pass is opt-in; when the
    provider is absent or fails, the lists are empty and the script reports
    the deterministic report only.

    Attributes:
        model_config: Forbid extra keys.
        contradictions: Claims on different pages that tension each other.
        stale: Claims a newer page has superseded (with page hint).
        gaps: Concepts mentioned but lacking a dedicated page.
    """

    model_config = ConfigDict(extra="forbid")

    contradictions: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when no LLM findings exist.

        Returns:
            bool: True on success.
        """
        return not (self.contradictions or self.stale or self.gaps)


__all__ = [
    "DeepLintReport",
    "IngestResult",
    "LintReport",
    "WikiExtraction",
    "WikiHit",
    "WikiThesis",
    "WikiVerdict",
]

"""Wiki layer schemas (ADR wiki-loop0): hits, ingest verdicts, lint reports.

The wiki layer is deterministic in Loop 0: no LLM calls, no embeddings. Hits
come from index/title scoring over ``<wiki_root>``; ingest files the rendered
article as a source page with a SHA256 cache so unchanged sources never
re-write. Every type is Pydantic and cross-port safe.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


__all__ = ["IngestResult", "LintReport", "WikiHit", "WikiVerdict"]

"""Ports: abstract contracts the core depends on (ADR 0001).

Adapters implement these; the core never imports a vendor SDK. Every port
exchanges only Pydantic types defined under :mod:`knowledge_engine.schemas`.
"""

from __future__ import annotations

import abc
from typing import Any

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.browser import BrowserActionResult
from knowledge_engine.schemas.clean import CleanResult
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import ChatRequest, ChatResult
from knowledge_engine.schemas.source import BodyRef, DocumentBody, SourceRecord
from knowledge_engine.schemas.wiki import IngestResult, LintReport, WikiHit


class ProviderPort(abc.ABC):
    """LLM transport. Implemented by FallbackRouter over concrete adapters."""

    @abc.abstractmethod
    async def complete(self, req: ChatRequest) -> ChatResult:
        """Complete a chat request.

        Args:
            req: Chat request.

        Returns:
            ChatResult: Chat result.
        """
        ...

    @abc.abstractmethod
    async def extract(self, req: ExtractionRequest) -> object:
        """Structured extraction; returns an instance of req.schema_ref's model.

        Args:
            req: Extraction request.

        Returns:
            object: Instance of the requested schema model.
        """
        ...


class SearchPort(abc.ABC):
    """Federated scholarly search."""

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Search scholarly sources.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            list[SourceRecord]: Resolved scholarly sources.
        """
        ...


class FetchPort(abc.ABC):
    """DOI/ID-first resolution (free OpenAlex path; preserves search quota)."""

    @abc.abstractmethod
    async def fetch(self, doi: str) -> SourceRecord | None:
        """Fetch a record by DOI.

        Args:
            doi: Digital object identifier.

        Returns:
            SourceRecord | None: Resolved record, or None when unknown.
        """
        ...


class ScrapePort(abc.ABC):
    """URL -> LLM-ready Markdown."""

    @abc.abstractmethod
    async def to_markdown(self, url: str) -> DocumentBody:
        """Fetch a URL as LLM-ready Markdown.

        Args:
            url: Target URL.

        Returns:
            DocumentBody: Markdown body.
        """
        ...


class BodyStoragePort(abc.ABC):
    """Persist scraped bodies durably; workflow state carries BodyRef pointers (ADR 0010).

    Adapters decide WHERE bodies live (local files today, Supabase Storage
    later) — the port keeps nodes free of storage backends (DIP).
    """

    @abc.abstractmethod
    async def save(self, body: DocumentBody) -> BodyRef:
        """Persist a body; return its deterministic lightweight reference.

        Args:
            body: The scraped body (markdown + metadata).

        Returns:
            BodyRef: Reference usable by :meth:`load`.
        """
        ...

    @abc.abstractmethod
    async def load(self, ref: BodyRef) -> str:
        """Retrieve the markdown a reference points at.

        Args:
            ref: Reference returned by :meth:`save`.

        Returns:
            str: Markdown content.
        """
        ...


class BrowserPort(abc.ABC):
    """Full browser automation: render + interact with live pages."""

    @abc.abstractmethod
    async def navigate(self, url: str, *, wait_selector: str = "body") -> BrowserActionResult:
        """Navigate to a URL.

        Args:
            url: Target URL.
            wait_selector: Selector to wait for.

        Returns:
            BrowserActionResult: Navigation outcome.
        """
        ...

    @abc.abstractmethod
    async def click(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Click an element.

        Args:
            selector: CSS selector.
            timeout_ms: Wait timeout in milliseconds.

        Returns:
            BrowserActionResult: Click outcome.
        """
        ...

    @abc.abstractmethod
    async def fill(self, selector: str, value: str) -> BrowserActionResult:
        """Fill an input field.

        Args:
            selector: CSS selector.
            value: Value to fill.

        Returns:
            BrowserActionResult: Fill outcome.
        """
        ...

    @abc.abstractmethod
    async def submit(self, selector: str | None = None) -> BrowserActionResult:
        """Submit a form.

        Args:
            selector: Optional submit-button selector.

        Returns:
            BrowserActionResult: Submit outcome.
        """
        ...

    @abc.abstractmethod
    async def extract_text(self, selector: str = "body") -> BrowserActionResult:
        """Extract visible text.

        Args:
            selector: CSS selector scope.

        Returns:
            BrowserActionResult: Extraction outcome.
        """
        ...

    @abc.abstractmethod
    async def extract_markdown(self) -> BrowserActionResult:
        """Extract the page as Markdown.

        Returns:
            BrowserActionResult: Extraction outcome.
        """
        ...

    @abc.abstractmethod
    async def screenshot(self, path: str | None = None) -> BrowserActionResult:
        """Capture a screenshot.

        Args:
            path: Optional output path.

        Returns:
            BrowserActionResult: Screenshot outcome.
        """
        ...

    @abc.abstractmethod
    async def assert_visible(self, selector: str, *, timeout_ms: int = 10_000) -> BrowserActionResult:
        """Assert an element is visible.

        Args:
            selector: CSS selector.
            timeout_ms: Wait timeout in milliseconds.

        Returns:
            BrowserActionResult: Assertion outcome.
        """
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """Release browser resources."""
        ...


class BrowserActionPort(abc.ABC):
    """Serializable action surface for agent tools (MCP ``browser_act``)."""

    @abc.abstractmethod
    async def act(self, action: str, **kwargs: Any) -> BrowserActionResult:
        """Dispatch a named browser action.

        Args:
            action: Action name (navigate/click/fill/submit/...).
            **kwargs: Action-specific arguments.

        Returns:
            BrowserActionResult: Action outcome.
        """
        ...


class ArtifactStorePort(abc.ABC):
    """Durable store for published artifacts (ADR-0012, issue #17).

    Sync surface (documented trade-off): ``ResearchStore`` writes are already
    sync and ``supabase-py`` is sync. Adapters decide WHERE published
    articles + sources live — local files (default, portable) or Supabase
    Storage (cloud, survives ephemeral hosts).
    """

    @abc.abstractmethod
    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        """Persist the rendered article; return a locator (path or URL).

        Args:
            thread_id: Run thread identifier.
            artifact: The final artifact.

        Returns:
            str: Locator for downstream reads.
        """
        ...

    @abc.abstractmethod
    def put_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        """Persist resolved sources; return a locator (path or URL).

        Args:
            thread_id: Run thread identifier.
            sources: Resolved source records.

        Returns:
            str: Locator for downstream reads.
        """
        ...

    @abc.abstractmethod
    def get_article(self, thread_id: str) -> str | None:
        """Return the article markdown, or None when absent.

        Args:
            thread_id: Run thread identifier.

        Returns:
            str | None: Article text or None.
        """
        ...

    @abc.abstractmethod
    def get_sources(self, thread_id: str) -> list[SourceRecord]:
        """Return the persisted sources for a thread.

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[SourceRecord]: Persisted sources (empty when absent).
        """
        ...


class WikiQueryPort(abc.ABC):
    """Search the compiled wiki before live federated search (ADR wiki-loop0).

    Adapters decide where wiki pages live (local markdown, Supabase Storage);
    hits are deterministic, citation-ready pointers — never synthesized text.
    """

    @abc.abstractmethod
    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Return the best-matching wiki pages for a query.

        Args:
            query: Research query string.
            limit: Maximum hits to return.

        Returns:
            list[WikiHit]: Ranked hits (empty when the wiki has no match).
        """
        ...

    @abc.abstractmethod
    async def read_page(self, slug: str) -> str | None:
        """Read one wiki page's markdown by slug.

        Args:
            slug: Page slug (filename stem or bucket key suffix).

        Returns:
            str | None: Page text, or None when absent.
        """
        ...


class WikiIngestPort(abc.ABC):
    """File rendered research into the wiki (the compile step of the pattern).

    Adapters apply the New/Update/Disputed/No-material verdicts, maintain
    ``index.md``/``log.md``/``overview.md``, and keep a SHA256 content cache
    so unchanged sources never rewrite pages.
    """

    @abc.abstractmethod
    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """File one rendered article into the wiki.

        Args:
            thread_id: Run thread identifier (provenance for the page).
            query: Original research query (page title source).
            article_path: Path of the rendered ``article.md`` to ingest.

        Returns:
            IngestResult: Verdict + write counts.
        """
        ...


class CleanPort(abc.ABC):
    """Drop duplicate sources before relevance filtering (close-never-delete).

    Adapters implement the tiered pipeline (exact keys -> MinHash near-dup ->
    containment); the port exposes only the batch operation so nodes stay
    free of hashing internals (ISP/DIP).
    """

    @abc.abstractmethod
    async def clean(self, sources: list[SourceRecord]) -> CleanResult:
        """Clean one batch of source records.

        Args:
            sources: Raw federated sources for the run.

        Returns:
            CleanResult: Kept records plus superseded-by pointers.
        """
        ...


class VaultLintPort(abc.ABC):
    """Health-check the wiki vault deterministically (no LLM in Loop 0)."""

    @abc.abstractmethod
    async def lint(self) -> LintReport:
        """Scan the vault and report structural issues.

        Returns:
            LintReport: Findings (broken links, orphans, frontmatter, thin
            pages, index drift).
        """
        ...

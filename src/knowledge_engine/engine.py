"""ResearchEngine — the library's portable public facade.

One import surface another project uses to do research without learning the
internal ports/adapters:

* ``await engine.research(query)`` — run the LangGraph research workflow,
  writing artifacts to ``research_store/`` (files-only).
* ``await engine.search(query)`` — quick scholarly search.
* ``await engine.scrape(url)`` — URL → LLM-ready Markdown.
* ``await engine.browser.act(...)`` — full browser automation.
* ``await engine.discoverer.select()`` — find a working free LLM.

Everything is constructed once (composition root) and reused across calls;
the results folder is plain files so moving projects = moving the folder.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from knowledge_engine.config import load_dotenv
from knowledge_engine.core.ports import BrowserActionPort, ScrapePort, SearchPort
from knowledge_engine.infrastructure.discovery.service import ModelDiscoverer
from knowledge_engine.infrastructure.scrape.scrape_router import ScrapeRouter
from knowledge_engine.infrastructure.search.demux import DemuxSearchAdapter
from knowledge_engine.orchestration.graphs import NodeSet, build_workflow
from knowledge_engine.orchestration.nodes import (
    BodiesNode,
    FetchBodyNode,
    IngestNode,
    JudgeNode,
    RelevanceNode,
    RenderNode,
    ReviewNode,
    SynthesizeNode,
)
from knowledge_engine.orchestration.runtime import GraphRuntime, RunResult
from knowledge_engine.schemas.browser import BrowserActionResult
from knowledge_engine.schemas.source import DocumentBody, SourceRecord
from knowledge_engine.store.file_checkpointer import FileCheckpointer
from knowledge_engine.store.research_store import ResearchStore


@dataclass
class ResearchEngine:
    """High-level research library surface (files-only persistence)."""

    store_root: str = "research_store"
    search_limit: int = 10
    auto_approve_review: bool = True
    # Optional injection points; defaults are built lazily from env.
    search: SearchPort | None = None
    scrape: ScrapePort | None = None
    provider: Any | None = None
    browser: BrowserActionPort | None = None
    discoverer: ModelDiscoverer | None = None
    _store: ResearchStore = field(init=False)
    _checkpointer: FileCheckpointer = field(init=False)
    _search: SearchPort = field(init=False)
    _scrape: ScrapePort = field(init=False)
    _provider: Any = field(init=False)
    _discoverer: ModelDiscoverer = field(init=False)

    def __post_init__(self) -> None:
        load_dotenv()
        self._store = ResearchStore(self.store_root)
        self._checkpointer = FileCheckpointer(f"{self.store_root}/checkpoints")
        self._search = self.search or _default_search()
        self._scrape = self.scrape or ScrapeRouter()
        self._provider = self.provider or _default_provider()
        self._discoverer = self.discoverer or _default_discoverer()

    @property
    def store(self) -> ResearchStore:
        """The files-only persistence store backing this engine."""
        return self._store

    @property
    def checkpointer(self) -> FileCheckpointer:
        """File-based LangGraph checkpointer (durability + HITL)."""
        return self._checkpointer

    def _nodeset(self, *, interactive_review: bool | None = None) -> NodeSet:
        review = ReviewNode(
            interactive=bool(interactive_review if interactive_review is not None else not self.auto_approve_review)
        )
        return NodeSet(
            ingest=IngestNode(self._search, limit=self.search_limit),
            relevance=RelevanceNode(self._provider),
            bodies=BodiesNode(),
            fetch_body=FetchBodyNode(self._scrape),
            synthesize=SynthesizeNode(self._provider),
            judge=JudgeNode(self._provider),
            review=review,
            render=RenderNode(root=self.store_root),
        )

    def _runtime_for(self, workflow: str) -> GraphRuntime:
        runtime = GraphRuntime(nodes=self._nodeset(), checkpointer=self._checkpointer)
        runtime.graph(workflow)  # pre-compile
        return runtime

    async def research(
        self, query: str, *, workflow: str = "research", thread_id: str | None = None, unique: bool = False
    ) -> RunResult:
        """Run a research workflow and return + persist its durable state.

        Args:
            query: Research question to investigate.
            workflow: One of ``research``, ``qa``, ``summary``.
            thread_id: Optional stable id; derived from the query when absent.
            unique: When True, append a random suffix so parallel duplicate
                submissions of the same query land on separate LangGraph
                threads instead of colliding on one.

        Returns:
            RunResult: The run's durable state + any pending review interrupt.
        """
        tid = thread_id or _derived_thread_id(query)
        if unique:
            tid = f"{tid}-{uuid.uuid4().hex[:8]}"
        runtime = self._runtime_for(workflow)
        self._store.write_metadata(
            tid,
            {"query": query, "workflow": workflow, "status": "running", "provider": "discovery"},
        )
        result = await runtime.run(workflow, tid, query)
        self._store.write_metadata(tid, result.state.model_dump(mode="json") | {"query": query})
        self._store.write_sources(tid, result.state.sources)
        self._store.append_event(tid, {"event": "run.completed", "thread_id": tid})
        return result

    async def resume(self, workflow: str, thread_id: str, payload: Any) -> RunResult:
        """Resume a run paused at the review ``interrupt()``.

        Args:
            workflow: The workflow that was running.
            thread_id: The thread that paused.
            payload: Human decision (e.g. ``{"approved": True}``).

        Returns:
            RunResult: The resumed run's durable state.
        """
        runtime = self._runtime_for(workflow)
        result = await runtime.resume(workflow, thread_id, payload)
        self._store.write_metadata(thread_id, result.state.model_dump(mode="json"))
        return result

    async def search_run(self, query: str, *, limit: int | None = None) -> list[SourceRecord]:
        """Quick scholarly search without running the full workflow.

        Args:
            query: Search terms.
            limit: Max results (defaults to the engine's ``search_limit``).

        Returns:
            list[SourceRecord]: The resolved scholarly sources.
        """
        return await self._search.search(query, limit=limit or self.search_limit)

    async def scrape_run(self, url: str) -> DocumentBody:
        """Fetch a URL to LLM-ready Markdown.

        Args:
            url: Target URL.

        Returns:
            DocumentBody: The URL's LLM-ready Markdown (or a scrape-adapted body).
        """
        return await self._scrape.to_markdown(url)

    async def browser_act(self, action: str, **kwargs: Any) -> BrowserActionResult:
        """Run a full browser automation action (scrape/forms/UI tests).

        Args:
            action: ``navigate``/``click``/``fill``/``submit``/``extract_text``
                /``extract_markdown``/``screenshot``/``assert_visible``.
            **kwargs: Action-specific arguments.

        Returns:
            BrowserActionResult: The action outcome (typed, JSON-safe).

        Raises:
            RuntimeError: If no browser adapter is configured.
        """
        if self.browser is None:
            raise RuntimeError("browser not configured; install [browser] extra and pass a browser adapter")
        return await self.browser.act(action, **kwargs)

    async def discover(self, *, preferred: str | None = None) -> str:
        """Discover a currently-working free LLM (health-checked).

        Args:
            preferred: A model id to try first (e.g. a previously selected one).

        Returns:
            str: The model id of the first free model that passed a cheap health check.

        Raises:
            ModelSelectionError: if no free model works.
        """
        return await self._discoverer.select(preferred=preferred)


def _derived_thread_id(query: str) -> str:
    """Derive a stable thread id from a query (deterministic across processes).

    ``hash()`` is seeded per-process (PYTHONHASHSEED), so it yields a *different*
    id on every invocation — useless for restoring a thread. A content hash is
    stable so re-submitting the same query resumes the same run.
    """
    digest = hashlib.sha1(query.strip().encode("utf-8")).hexdigest()[:12]
    return f"t-{digest}"


def _default_search() -> SearchPort:
    from knowledge_engine.infrastructure.search.biorxiv import BiorxivAdapter
    from knowledge_engine.infrastructure.search.dblp import DblpAdapter
    from knowledge_engine.infrastructure.search.doaj import DoajAdapter
    from knowledge_engine.infrastructure.search.europe_pmc import EuropePmcAdapter
    from knowledge_engine.infrastructure.search.huggingface_papers import HuggingFacePapersAdapter
    from knowledge_engine.infrastructure.search.openalex import OpenAlexAdapter
    from knowledge_engine.infrastructure.search.pubmed import PubMedAdapter
    from knowledge_engine.infrastructure.search.semantic_scholar import SemanticScholarAdapter
    from knowledge_engine.infrastructure.search.unpaywall import UnpaywallAdapter, is_usable_email

    # Order: DOI backbone first, biomedical depth next, then community/CS,
    # preprints, OA. Unpaywall last: DOI-only fetch that fills OA-PDF gaps.
    sources: list[SearchPort] = [
        OpenAlexAdapter(os.environ.get("OPENALEX_MAILTO", "")),
        PubMedAdapter(),
        EuropePmcAdapter(),
        HuggingFacePapersAdapter(),
        SemanticScholarAdapter(os.environ.get("SEMANTIC_SCHOLAR_API_KEY")),
        BiorxivAdapter("biorxiv"),
        BiorxivAdapter("medrxiv"),
        DblpAdapter(),
        DoajAdapter(),
    ]
    unpaywall_email = os.environ.get("UNPAYWALL_EMAIL", "")
    if is_usable_email(unpaywall_email):
        sources.append(UnpaywallAdapter(unpaywall_email))
    return DemuxSearchAdapter(sources)


def _default_provider() -> Any:
    from knowledge_engine.infrastructure.providers import (
        GeminiAdapter,
        GroqAdapter,
        MistralAdapter,
        OpenRouterAdapter,
    )
    from knowledge_engine.infrastructure.rate_limit import TokenBucketLimiter
    from knowledge_engine.infrastructure.router import FallbackRouter

    return FallbackRouter(
        [GroqAdapter(), GeminiAdapter(), MistralAdapter(), OpenRouterAdapter()],
        limiter=TokenBucketLimiter(),
    )


def _default_discoverer() -> ModelDiscoverer:
    return ModelDiscoverer.from_settings(
        groq_key=os.environ.get("GROQ_API_KEY"),
        mistral_key=os.environ.get("MISTRAL_API_KEY"),
        openrouter_key=os.environ.get("OPENROUTER_API_KEY"),
        gemini_key=os.environ.get("GEMINI_API_KEY"),
    )


__all__ = ["ResearchEngine", "build_workflow", "RunResult"]

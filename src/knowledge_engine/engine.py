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
    AggregateNode,
    BodiesNode,
    BranchNode,
    CriticNode,
    DecomposeNode,
    FetchBodyNode,
    IngestNode,
    IntentNode,
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
    """High-level research library surface (files by default, Supabase opt-in).

    Attributes:
        store_root: Attribute description.
        search_limit: Attribute description.
        auto_approve_review: Attribute description.
        checkpointer_kind: ``file`` (default, portable) or ``postgres`` (Supabase).
        store_kind: ``memory`` (default) or ``postgres`` (Supabase long-term memory).
        database_url: Supabase session-pooler DSN; falls back to ``DATABASE_URL`` env.
        cleanup_after_run: Delete the thread's checkpoints after a finished run.
        search: Attribute description.
        scrape: Attribute description.
        provider: Attribute description.
        browser: Attribute description.
        discoverer: Attribute description.
    """

    store_root: str = "research_store"
    search_limit: int = 10
    auto_approve_review: bool = True
    checkpointer_kind: str = "file"
    store_kind: str = "memory"
    database_url: str | None = None
    cleanup_after_run: bool = False
    # Optional injection points; defaults are built lazily from env.
    search: SearchPort | None = None
    scrape: ScrapePort | None = None
    provider: Any | None = None
    browser: BrowserActionPort | None = None
    discoverer: ModelDiscoverer | None = None
    _store: ResearchStore = field(init=False)
    _checkpointer: Any = field(init=False)
    _memory_store: Any = field(init=False)
    _checkpointer_cm: Any = field(init=False, default=None)
    _memory_store_cm: Any = field(init=False, default=None)
    _search: SearchPort = field(init=False)
    _scrape: ScrapePort = field(init=False)
    _provider: Any = field(init=False)
    _discoverer: ModelDiscoverer = field(init=False)

    def __post_init__(self) -> None:
        """Initialize derived components."""
        load_dotenv()
        self._store = ResearchStore(self.store_root)
        self._checkpointer = self._build_checkpointer()
        self._memory_store = self._build_memory_store()
        self._search = self.search or _default_search()
        self._scrape = self.scrape or ScrapeRouter()
        self._provider = self.provider or _default_provider()
        self._discoverer = self.discoverer or _default_discoverer()

    def _dsn(self) -> str | None:
        """Return the configured Supabase DSN, if any.

        Returns:
            str | None: DSN or None.
        """
        return self.database_url or os.environ.get("DATABASE_URL")

    def _build_checkpointer(self) -> Any:
        """Build the LangGraph checkpointer for the configured kind.

        Postgres returns an async context manager (no running loop at build
        time); call ``await setup()`` once to enter it.

        Returns:
            Any: Checkpointer instance or context manager.
        """
        if self.checkpointer_kind == "file":
            return FileCheckpointer(f"{self.store_root}/checkpoints")
        from knowledge_engine.orchestration.checkpoint import build_checkpointer

        cm = build_checkpointer(self.checkpointer_kind, conn_string=self._dsn())
        if self.checkpointer_kind == "postgres":
            self._checkpointer_cm = cm
            return None
        return cm

    def _build_memory_store(self) -> Any:
        """Build the LangGraph cross-thread store for the configured kind.

        Returns:
            Any: Store instance or context manager (postgres; see above).
        """
        from knowledge_engine.orchestration.store_factory import build_store

        if self.store_kind == "memory":
            return build_store("memory")
        cm = build_store(self.store_kind, conn_string=self._dsn())
        if self.store_kind == "postgres":
            self._memory_store_cm = cm
            return None
        return cm

    async def setup(self) -> None:
        """Enter Supabase resources and create tables (postgres kinds only).

        No-op for ``file``/``memory``. Calls ``setup()`` on the entered saver
        and store (creates ``checkpoints``/``stores`` tables on first run).
        """
        from knowledge_engine.orchestration.checkpoint import setup_postgres

        if self._checkpointer_cm is not None and self._checkpointer is None:
            self._checkpointer = await self._checkpointer_cm.__aenter__()
        if self._memory_store_cm is not None and self._memory_store is None:
            self._memory_store = await self._memory_store_cm.__aenter__()
        entered = [r for r in (self._checkpointer, self._memory_store) if r is not None and self._uses_postgres()]
        if entered:
            await setup_postgres(*entered)

    def _uses_postgres(self) -> bool:
        """Return True when either persistence kind is postgres.

        Returns:
            bool: True on success.
        """
        return self.checkpointer_kind == "postgres" or self.store_kind == "postgres"

    async def close(self) -> None:
        """Exit Supabase resources (postgres kinds only)."""
        if self._memory_store_cm is not None and self._memory_store is not None:
            await self._memory_store_cm.__aexit__(None, None, None)
            self._memory_store = None
        if self._checkpointer_cm is not None and self._checkpointer is not None:
            await self._checkpointer_cm.__aexit__(None, None, None)
            self._checkpointer = None

    @property
    def store(self) -> ResearchStore:
        """The files-only persistence store backing this engine.

        Returns:
            ResearchStore: Persistence store.

        """
        return self._store

    @property
    def checkpointer(self) -> Any:
        """LangGraph checkpointer (file-based by default, Supabase opt-in).

        Returns:
            Any: Checkpoint saver.
        """
        return self._checkpointer

    @property
    def memory_store(self) -> Any:
        """LangGraph cross-thread store (memory by default, Supabase opt-in).

        Returns:
            Any: Store instance.
        """
        return self._memory_store

    def _nodeset(self, *, interactive_review: bool | None = None) -> NodeSet:
        """Build the node set.

        Args:
            interactive_review: Whether review is interactive.

        Returns:
            NodeSet: Node bundle.



        """
        review = ReviewNode(
            interactive=bool(interactive_review if interactive_review is not None else not self.auto_approve_review)
        )
        ingest = IngestNode(self._search, limit=self.search_limit)
        relevance = RelevanceNode(self._provider)
        fetch_body = FetchBodyNode(self._scrape)
        synthesize = SynthesizeNode(self._provider)
        return NodeSet(
            ingest=ingest,
            relevance=relevance,
            bodies=BodiesNode(),
            fetch_body=fetch_body,
            synthesize=synthesize,
            judge=JudgeNode(self._provider),
            review=review,
            render=RenderNode(root=self.store_root),
            intent=IntentNode(provider=self._provider),
            decompose=DecomposeNode(provider=self._provider),
            branch=BranchNode(
                ingest=IngestNode(self._search, limit=min(self.search_limit, 5)),
                relevance=relevance,
                fetch_body=fetch_body,
                synthesize=synthesize,
            ),
            aggregate=AggregateNode(provider=self._provider),
            critic=CriticNode(provider=self._provider),
        )

    def compiled_graph(self, workflow: str = "deep") -> Any:
        """Return the compiled graph for Studio/deploys (public surface).

        Args:
            workflow: One of ``research``, ``qa``, ``summary``, ``deep``.

        Returns:
            Any: Compiled graph.
        """
        return self._runtime_for(workflow).graph(workflow)

    def _runtime_for(self, workflow: str) -> GraphRuntime:
        """Build the runtime for a workflow.

        Args:
            workflow: Workflow name to run.

        Returns:
            GraphRuntime: Graph runtime.

        Raises:
            ConfigurationError: When a postgres kind was requested but
                ``await setup()`` was not called first.
        """
        if (self.checkpointer_kind == "postgres" and self._checkpointer is None) or (
            self.store_kind == "postgres" and self._memory_store is None
        ):
            from knowledge_engine.core.exceptions import ConfigurationError

            raise ConfigurationError("postgres persistence requested: call 'await engine.setup()' first")
        runtime = GraphRuntime(nodes=self._nodeset(), checkpointer=self._checkpointer, store=self._memory_store)
        runtime.graph(workflow)  # pre-compile
        return runtime

    async def research(
        self, query: str, *, workflow: str = "research", thread_id: str | None = None, unique: bool = False
    ) -> RunResult:
        """Run a research workflow and return + persist its durable state.

        Args:
            query: Research question to investigate.
            workflow: One of ``research``, ``qa``, ``summary``, ``deep``.
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
        if self.cleanup_after_run and not result.interrupted:
            await runtime.delete_thread(tid)
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
        """
        return await self._discoverer.select(preferred=preferred)


def _derived_thread_id(query: str) -> str:
    """Derive a stable thread id from a query (deterministic across processes).

        ``hash()`` is seeded per-process (PYTHONHASHSEED), so it yields a *different*
        id on every invocation — useless for restoring a thread. A content hash is
        stable so re-submitting the same query resumes the same run.

    Args:
        query: Scholarly query string.

    Returns:
        str: Result text.

    """
    digest = hashlib.sha1(query.strip().encode("utf-8")).hexdigest()[:12]
    return f"t-{digest}"


def _default_search() -> SearchPort:
    """Build the default federated search.

    Returns:
        SearchPort: Search adapter.


    """
    from knowledge_engine.infrastructure.search.biorxiv import BiorxivAdapter
    from knowledge_engine.infrastructure.search.crossref import CrossrefAdapter
    from knowledge_engine.infrastructure.search.dblp import DblpAdapter
    from knowledge_engine.infrastructure.search.doaj import DoajAdapter
    from knowledge_engine.infrastructure.search.europe_pmc import EuropePmcAdapter
    from knowledge_engine.infrastructure.search.huggingface_papers import HuggingFacePapersAdapter
    from knowledge_engine.infrastructure.search.openalex import OpenAlexAdapter
    from knowledge_engine.infrastructure.search.osf import OSFPreprintsAdapter
    from knowledge_engine.infrastructure.search.pubmed import PubMedAdapter
    from knowledge_engine.infrastructure.search.semantic_scholar import SemanticScholarAdapter
    from knowledge_engine.infrastructure.search.unpaywall import UnpaywallAdapter, is_usable_email

    # Order: DOI backbone first (OpenAlex, then Crossref registry fallback),
    # biomedical depth next, then community/CS, preprints, OA. Unpaywall last:
    # DOI-only fetch that fills OA-PDF gaps.
    sources: list[SearchPort] = [
        OpenAlexAdapter(os.environ.get("OPENALEX_MAILTO", "")),
        CrossrefAdapter(os.environ.get("CROSSREF_MAILTO", "")),
        PubMedAdapter(),
        EuropePmcAdapter(),
        HuggingFacePapersAdapter(),
        OSFPreprintsAdapter(),
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
    """Build the default provider router.

    Returns:
        Any: Result.

    """
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
    """Build the default model discoverer.

    Returns:
        ModelDiscoverer: Configured discoverer.


    """
    return ModelDiscoverer.from_settings(
        groq_key=os.environ.get("GROQ_API_KEY"),
        mistral_key=os.environ.get("MISTRAL_API_KEY"),
        openrouter_key=os.environ.get("OPENROUTER_API_KEY"),
        gemini_key=os.environ.get("GEMINI_API_KEY"),
    )


__all__ = ["ResearchEngine", "build_workflow", "RunResult"]

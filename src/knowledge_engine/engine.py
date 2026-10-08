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
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.browser import BrowserActionResult
from knowledge_engine.schemas.settings import RunSettings
from knowledge_engine.schemas.source import DocumentBody, SourceRecord
from knowledge_engine.store.body_file_store import LocalBodyStorage
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
        artifact_store_kind: ``local`` (default, portable) or ``supabase`` (cloud storage).
        artifacts: Optional prebuilt ArtifactStorePort override (tests/composition).
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
    artifact_store_kind: str = "local"
    artifacts: Any | None = None
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
        self._store = self._build_research_store()
        self._checkpointer = self._build_checkpointer()
        self._memory_store = self._build_memory_store()
        self._search = self.search or _default_search(self.store_root)
        self._scrape = self.scrape or ScrapeRouter()
        self._provider = self.provider or _default_provider()
        self._discoverer = self.discoverer or _default_discoverer()

    def _dsn(self) -> str | None:
        """Return the configured Supabase DSN, if any.

        Returns:
            str | None: DSN or None.
        """
        return self.database_url or os.environ.get("DATABASE_URL")

    def _build_research_store(self) -> ResearchStore:
        """Build the files store with the configured artifact persistence (T4).

        Returns:
            ResearchStore: Files-only run folders over the artifact store.

        Raises:
            ConfigurationError: When ``artifact_store_kind='supabase'`` lacks keys
                or the extra.
        """
        from knowledge_engine.core.exceptions import ConfigurationError

        if self.artifacts is not None:
            return ResearchStore(self.store_root, artifacts=self.artifacts)
        if self.artifact_store_kind == "supabase":
            from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore

            artifacts = SupabaseArtifactStore.from_env()
        elif self.artifact_store_kind == "local":
            artifacts = None
        else:
            raise ConfigurationError(
                f"unknown artifact_store_kind: {self.artifact_store_kind!r} (use 'local' or 'supabase')"
            )
        return ResearchStore(self.store_root, artifacts=artifacts)

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
        storage = LocalBodyStorage(root=f"{self.store_root}/bodies")
        fetch_body = FetchBodyNode(self._scrape, storage=storage)
        synthesize = SynthesizeNode(self._provider, storage=storage)
        render_save = _artifact_saver(self._store)
        return NodeSet(
            ingest=ingest,
            relevance=relevance,
            bodies=BodiesNode(),
            fetch_body=fetch_body,
            synthesize=synthesize,
            judge=JudgeNode(self._provider),
            review=review,
            render=RenderNode(root=self.store_root, save=render_save),
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
        self,
        query: str,
        *,
        workflow: str = "research",
        thread_id: str | None = None,
        unique: bool = False,
        settings: RunSettings | None = None,
    ) -> RunResult:
        """Run a research workflow and return + persist its durable state.

        Args:
            query: Research question to investigate.
            workflow: One of ``research``, ``qa``, ``summary``, ``deep``.
            thread_id: Optional stable id; derived from the query when absent.
            unique: When True, append a random suffix so parallel duplicate
                submissions of the same query land on separate LangGraph
                threads instead of colliding on one.
            settings: Per-run overrides (ADR 0016); validated here once.

        Returns:
            RunResult: The run's durable state + any pending review interrupt.
        """
        tid = thread_id or _derived_thread_id(query)
        if unique:
            tid = f"{tid}-{uuid.uuid4().hex[:8]}"
        settings_dump = settings.model_dump() if settings else None
        runtime = self._runtime_for(workflow)
        self._store.write_metadata(
            tid,
            {
                "query": query,
                "workflow": workflow,
                "status": "running",
                "provider": "discovery",
                **({"settings": settings_dump} if settings_dump else {}),
            },
        )
        result = await runtime.run(workflow, tid, query, settings=settings_dump)
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


def _artifact_saver(store: ResearchStore) -> Any:
    """Return an async save callable routing RenderNode through the port (ADR-0012).

    Args:
        store: The files store whose artifact port persists published output.

    Returns:
        Any: Async callable ``(artifact) -> output_path`` writing through
            ``ResearchStore.write_article`` (port + local copy).
    """

    async def _save(artifact: KnowledgeArtifact) -> str:
        """Persist the artifact via the store and return the local locator.

        Args:
            artifact: Final knowledge artifact.

        Returns:
            str: Local article path.
        """
        store.write_article(artifact, artifact.id)
        return str(store.article_path(artifact.id))

    return _save


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


def _default_search(root: str = "research_store") -> SearchPort:
    """Build the default federated search.

    Args:
        root: Store root under which the web cache/quota files live.

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
    from knowledge_engine.infrastructure.search.wikipedia import WikipediaAdapter

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
    # General web last: fallback breadth after the scholarly set. Wikipedia is
    # keyless and stays a parallel demux source; the web chain (DuckDuckGo ->
    # Tavily -> Exa) is ONE sequential, cached source so a query spends at most
    # one provider's quota. Tavily/Exa join only when their keys are present.
    sources.append(WikipediaAdapter())
    sources.append(_default_web_chain(root))
    return DemuxSearchAdapter(sources)


def _default_web_chain(root: str) -> SearchPort:
    """Build the sequential, cached general-web fallback chain.

    DuckDuckGo (keyless) runs first; Tavily and Exa join only when their API
    keys are set, each guarded by a persisted monthly quota ledger. The whole
    chain is wrapped in a disk cache so a repeat query costs nothing.

    Args:
        root: Store root under which cache/quota files live.

    Returns:
        SearchPort: Cached fallback chain over the configured web providers.
    """
    from knowledge_engine.infrastructure.search.cache import CachedSearchAdapter, SearchCache
    from knowledge_engine.infrastructure.search.duckduckgo import DuckDuckGoAdapter
    from knowledge_engine.infrastructure.search.exa import EXA_MONTHLY_LIMIT, ExaAdapter
    from knowledge_engine.infrastructure.search.fallback import FallbackSearchAdapter
    from knowledge_engine.infrastructure.search.quota import MonthlyQuotaLedger
    from knowledge_engine.infrastructure.search.serper import SERPER_MONTHLY_LIMIT, SerperAdapter
    from knowledge_engine.infrastructure.search.tavily import TAVILY_MONTHLY_LIMIT, TavilyAdapter

    usage_root = f"{root}/_usage"
    providers: list[SearchPort] = [DuckDuckGoAdapter()]
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    if tavily_key:
        providers.append(
            TavilyAdapter(
                tavily_key,
                ledger=MonthlyQuotaLedger("tavily", limit=TAVILY_MONTHLY_LIMIT, root=usage_root),
            )
        )
    exa_key = os.environ.get("EXA_API_KEY", "")
    if exa_key:
        providers.append(
            ExaAdapter(
                exa_key,
                ledger=MonthlyQuotaLedger("exa", limit=EXA_MONTHLY_LIMIT, root=usage_root),
            )
        )
    serper_key = os.environ.get("SERPER_API_KEY", "")
    if serper_key:
        providers.append(
            SerperAdapter(
                serper_key,
                ledger=MonthlyQuotaLedger("serper", limit=SERPER_MONTHLY_LIMIT, root=usage_root),
            )
        )
    return CachedSearchAdapter(FallbackSearchAdapter(providers), SearchCache(f"{root}/_cache/search"))


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

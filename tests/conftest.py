"""Shared fixtures + fakes (offline, no network/Redis/MCP/LiveLLM).

The rate limiter/fallback router + the whole node/graph layer are unit-tested
against these doubles, preserving ADR 0008's "deterministic nodes run on fakes".
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator

import pytest

# psycopg async connections require a SelectorEventLoop; Windows defaults to
# the ProactorEventLoop. Set the Windows selector policy so async-Postgres
# tests run here (CI is Linux and is unaffected).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_engine.core.ports import ProviderPort, ScrapePort, SearchPort
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult, UsageSummary
from knowledge_engine.schemas.source import DocumentBody, FetchMethod, SearchSource, SourceRecord


class FakeArtifactStore:
    """In-memory artifact store satisfying the ArtifactStorePort contract.

    Attributes:
        calls: Recorded call log for spy assertions.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._articles: dict[str, str] = {}
        self._sources: dict[str, str] = {}

    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        """Persist article bytes in memory.

        Args:
            thread_id: Run thread identifier.
            artifact: Final artifact.

        Returns:
            str: Locator.
        """
        self.calls.append(f"{thread_id}:article")
        self._articles[thread_id] = f"---{artifact.frontmatter_yaml}---{artifact.body_markdown}"
        return f"memory://articles/{thread_id}"

    def put_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        """Persist sources in memory.

        Args:
            thread_id: Run thread identifier.
            sources: Resolved sources.

        Returns:
            str: Locator.
        """
        self.calls.append(f"{thread_id}:sources")
        self._sources[thread_id] = json.dumps([s.model_dump(mode="json") for s in sources])
        return f"memory://sources/{thread_id}"

    def get_article(self, thread_id: str) -> str | None:
        """Load in-memory article.

        Args:
            thread_id: Run thread identifier.

        Returns:
            str | None: Article text or None.
        """
        return self._articles.get(thread_id)

    def get_sources(self, thread_id: str) -> list[SourceRecord]:
        """Load in-memory sources.

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[SourceRecord]: Sources.
        """
        return [SourceRecord.model_validate(x) for x in json.loads(self._sources.get(thread_id, "[]"))]


class FakeSearch(SearchPort):
    """Returns one deterministic source per query."""

    def __init__(self, *, sources: list[SourceRecord] | None = None) -> None:
        self.sources = sources
        self.calls = 0

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        self.calls += 1
        if self.sources is not None:
            return self.sources
        return [
            SourceRecord(
                id="openalex:W1",
                title=f"{query} alpha",
                source=SearchSource.OPENALEX,
                pdf_url="https://example.org/paper.pdf",
            )
        ]


class FakeEmptySearch(FakeSearch):
    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        self.calls += 1
        return []


class FakeScrape(ScrapePort):
    def __init__(self, *, fail_urls: set[str] | None = None) -> None:
        self.fail_urls = fail_urls or set()
        self.calls: list[str] = []

    async def to_markdown(self, url: str) -> DocumentBody:
        self.calls.append(url)
        if url in self.fail_urls:
            from knowledge_engine.core.exceptions import ScrapeError

            raise ScrapeError(f"boom {url}")
        return DocumentBody(
            url=url, title="body", markdown=f"LLM-ready markdown for {url}", crawl_method=FetchMethod.CRAWL4AI
        )


class FakeProvider(ProviderPort):
    """Pluggable fake LLM: content depends on the task prompt/affinity."""

    def __init__(self, *, judge_pass: bool = True, review_allowed: bool = True) -> None:
        self.judge_pass = judge_pass
        self.review_allowed = review_allowed
        self.calls: list[str] = []

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls.append(req.affinity.value)
        user = req.messages[-1].content
        if req.affinity == Affinity.MICRO and "Research topic" in user:
            # batched relevance: keep every source id present in the ledger.
            # Ledger lines are "sid | title ..." (IDs only, no numeric
            # indices); a legacy "[i] sid | ..." shape is tolerated too.
            ids = []
            for line in user.splitlines():
                if " | " in line:
                    first = line.split(" | ")[0].strip()
                    if first.startswith("["):
                        first = first.rsplit(" ", 1)[-1].rstrip("]")
                    if first:
                        ids.append(first)
            text = json.dumps({"relevant_ids": ids})
        elif "classify intent" in user.lower():
            text = "deep"
        elif "sub-queries json" in user.lower():
            text = json.dumps(
                {
                    "sub_queries": [
                        {"query": "sub query one", "source": "search", "rationale": "covers aspect A"},
                        {"query": "sub query two", "source": "search", "rationale": "covers aspect B"},
                    ]
                }
            )
        elif req.affinity == Affinity.MICRO:
            text = "true"  # legacy per-source relevance path
        elif "critic rubric" in user.lower():
            text = json.dumps(
                {
                    "verdict": "sufficient",
                    "coverage": 0.8,
                    "diversity": 0.8,
                    "grounding": 0.8,
                    "contradictions": [],
                    "unanswered": [],
                    "revision_guidance": "evidence sufficient",
                }
            )
        elif "user prompt" in user or user.startswith("what is"):
            text = "agent answer"
        elif "score" in user.lower() or "groundedness" in user.lower():
            text = '{"score": 0.9, "pass": %s, "critique": ""}' % ("true" if self.judge_pass else "false")
        elif "reviewer feedback" in user.lower() or ("critique" in user.lower() and "pass" in user.lower()):
            text = "synthesis v2"
        else:
            text = "synthesis v1"
        return ChatResult.from_response(
            text,
            provider="fake",
            model="fake",
            usage=UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=0.0),
        )

    async def extract(self, req, response_model):  # pragma: no cover
        return None


@pytest.fixture
def fake_search() -> FakeSearch:
    return FakeSearch()


@pytest.fixture
def fake_scrape() -> FakeScrape:
    return FakeScrape()


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_provider_passing() -> FakeProvider:
    return FakeProvider(judge_pass=True)


@pytest.fixture
def fake_provider_failing() -> FakeProvider:
    return FakeProvider(judge_pass=False)


@pytest.fixture
async def runtime(fake_search, fake_scrape, fake_provider) -> AsyncIterator:
    """A research-workflow runtime wired to fakes + InMemorySaver."""
    from langgraph.checkpoint.memory import InMemorySaver

    from knowledge_engine.orchestration.graphs import NodeSet
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
    from knowledge_engine.orchestration.runtime import GraphRuntime

    nodes = NodeSet(
        ingest=IngestNode(fake_search),
        relevance=RelevanceNode(fake_provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(fake_scrape),
        synthesize=SynthesizeNode(fake_provider),
        judge=JudgeNode(fake_provider),
        review=ReviewNode(),
        render=RenderNode(root=".tmp"),
    )
    yield GraphRuntime(nodes=nodes, checkpointer=InMemorySaver())


__all__ = [
    "FakeEmptySearch",
    "FakeProvider",
    "FakeScrape",
    "FakeSearch",
    "SourceRecord",
    "fake_provider",
    "runtime",
]

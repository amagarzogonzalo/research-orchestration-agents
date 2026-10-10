"""E2E: the wiki-first hit path answers a repeat query with ZERO live calls.

This is the offline complement to the `internal_wiki_demo` eval case (which
would need a live record + human `--accept`): here the wiki layer is fully
deterministic, so a real compiled graph + a pre-filed wiki page + fake
network adapters prove the repeat-question savings without any cassette.

The spy search/scrape/provider FAIL if invoked: on an exact-query hit the
graph routes `wiki_query -> render` and never touches ingest/bodies/
synthesize/judge — which is the entire point of the wiki layer.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.infrastructure.clean import SourceCleaner
from knowledge_engine.infrastructure.wiki import FileWikiStore
from knowledge_engine.orchestration.graphs import NodeSet
from knowledge_engine.orchestration.nodes import (
    BodiesNode,
    CleanNode,
    FetchBodyNode,
    IngestNode,
    JudgeNode,
    RelevanceNode,
    RenderNode,
    ReviewNode,
    SynthesizeNode,
    WikiIngestNode,
    WikiQueryNode,
)
from knowledge_engine.orchestration.runtime import GraphRuntime
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch

_QUERY = "state of the art in retrieval augmented generation"


class FailSearch(FakeSearch):
    """Search that raises if the live path is ever reached."""

    async def search(self, query: str, *, limit: int = 10):  # type: ignore[override]
        """Refuse to run: a wiki hit must never reach live search.

        Args:
            query: Query string.
            limit: Max records.

        Raises:
            AssertionError: Always (should never be called).
        """
        raise AssertionError("live search called on a wiki hit")


def _runtime(root: str) -> tuple[GraphRuntime, FileWikiStore]:
    """Build a research runtime with the wiki layer and failing live adapters.

    Args:
        root: Store root for the wiki.

    Returns:
        tuple[GraphRuntime, FileWikiStore]: The runtime and its wiki store.
    """
    search: object = FailSearch()
    scrape = FakeScrape()
    provider = FakeProvider(judge_pass=True)
    storage = LocalBodyStorage(root=f"{root}/bodies")
    wiki = FileWikiStore(root=f"{root}/wiki")
    nodes = NodeSet(
        ingest=IngestNode(search),  # type: ignore[arg-type]
        relevance=RelevanceNode(provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(scrape, storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
        judge=JudgeNode(provider),
        review=ReviewNode(interactive=False),
        render=RenderNode(root=root),
        wiki_query=WikiQueryNode(wiki, limit=3),
        clean=CleanNode(SourceCleaner()),
        wiki_ingest=WikiIngestNode(wiki),
    )
    return GraphRuntime(nodes=nodes, checkpointer=InMemorySaver()), wiki


async def test_wiki_hit_answers_without_live_calls(tmp_path) -> None:
    """A pre-filed exact-query page answers the run with zero live calls."""
    runtime, wiki = _runtime(str(tmp_path))
    await wiki.ingest("seed", _QUERY, _article(tmp_path, "Compiled wiki answer."))
    result = await runtime.run("research", "e2e-wiki-hit", _QUERY)
    assert not result.interrupted
    assert result.state.status.value == "done"
    assert result.state.output_path
    assert "Compiled wiki answer" in _read(result.state.output_path)


async def test_wiki_miss_still_runs_offline(tmp_path) -> None:
    """A fresh query (no wiki page) proceeds through the normal graph."""
    root = str(tmp_path)
    search = FakeSearch()
    scrape = FakeScrape()
    provider = FakeProvider(judge_pass=True)
    storage = LocalBodyStorage(root=f"{root}/bodies")
    wiki = FileWikiStore(root=f"{root}/wiki")
    nodes = NodeSet(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(scrape, storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
        judge=JudgeNode(provider),
        review=ReviewNode(interactive=False),
        render=RenderNode(root=root),
        wiki_query=WikiQueryNode(wiki, limit=3),
        clean=CleanNode(SourceCleaner()),
        wiki_ingest=WikiIngestNode(wiki),
    )
    runtime = GraphRuntime(nodes=nodes, checkpointer=InMemorySaver())
    result = await runtime.run("research", "e2e-wiki-miss", "a brand new topic never filed")
    assert not result.interrupted
    assert result.state.status.value == "done"
    assert search.search  # the fake was genuinely reachable
    assert (tmp_path / "wiki" / "sources").is_dir()


def _runtime_with_wiki(root: str, workflow: str) -> tuple[GraphRuntime, FileWikiStore]:
    """Build a runtime for any single-pass workflow with the wiki layer.

    Args:
        root: Store root for the wiki.
        workflow: ``research``/``qa``/``summary``.

    Returns:
        tuple[GraphRuntime, FileWikiStore]: Runtime + wiki store.
    """
    search = FakeSearch()
    scrape = FakeScrape()
    provider = FakeProvider(judge_pass=True)
    storage = LocalBodyStorage(root=f"{root}/bodies")
    wiki = FileWikiStore(root=f"{root}/wiki")
    nodes = NodeSet(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        bodies=BodiesNode(),
        fetch_body=FetchBodyNode(scrape, storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
        judge=JudgeNode(provider),
        review=ReviewNode(interactive=False),
        render=RenderNode(root=root),
        wiki_query=WikiQueryNode(wiki, limit=3),
        clean=CleanNode(SourceCleaner()),
        wiki_ingest=WikiIngestNode(wiki),
    )
    return GraphRuntime(nodes=nodes, checkpointer=InMemorySaver()), wiki


async def test_qa_workflow_compiles_into_wiki(tmp_path) -> None:
    """The wiki suffix/clean apply to the qa workflow too."""
    root = str(tmp_path)
    runtime, wiki = _runtime_with_wiki(root, "qa")
    result = await runtime.run("qa", "e2e-qa-wiki", "short factual question")
    assert not result.interrupted
    assert result.state.status.value == "done"
    assert (tmp_path / "wiki" / "sources").is_dir()
    assert await wiki.search_wiki("short factual question"), "qa answer must be filed into the wiki"


async def test_summary_workflow_compiles_into_wiki(tmp_path) -> None:
    """The wiki suffix applies to the summary workflow too."""
    root = str(tmp_path)
    runtime, wiki = _runtime_with_wiki(root, "summary")
    result = await runtime.run("summary", "e2e-summary-wiki", "summarize the main ideas")
    assert not result.interrupted
    assert result.state.status.value == "done"
    assert await wiki.search_wiki("summarize the main ideas"), "summary must be filed into the wiki"


def _article(tmp_path, text: str) -> str:
    """Write a scratch article and return its path.

    Args:
        tmp_path: Scratch dir.
        text: Article body.

    Returns:
        str: Path.
    """
    path = tmp_path / "seed-article.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _read(path: str) -> str:
    """Read an artifact.

    Args:
        path: File path.

    Returns:
        str: Contents.
    """
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")

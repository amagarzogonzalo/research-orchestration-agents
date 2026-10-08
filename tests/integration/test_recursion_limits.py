"""Recursion-limit plumbing (ADR 0011): runtime injects the derived bound.

Two guarantees: ``GraphRuntime.run`` passes the derived workflow limit into
``ainvoke``'s config, and LangGraph actually enforces that key (a looping
graph with a tiny bound raises ``GraphRecursionError`` instead of spinning).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from knowledge_engine.orchestration.graphs import NodeSet
from knowledge_engine.orchestration.limits import DEFAULT_LIMITS
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
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _runtime(search=None, scrape=None, provider=None, root=None) -> GraphRuntime:
    """Build a runtime with fakes and an in-memory checkpointer.

    Args:
        search: Optional search fake override.
        scrape: Optional scrape fake override.
        provider: Optional provider fake override.
        root: Optional scratch root for body storage (a temp dir by default).

    Returns:
        GraphRuntime: Configured runtime.
    """
    import tempfile

    search, scrape, provider = search or FakeSearch(), scrape or FakeScrape(), provider or FakeProvider()
    storage = LocalBodyStorage(root=root or Path(tempfile.mkdtemp()) / "bodies")
    return GraphRuntime(
        nodes=NodeSet(
            ingest=IngestNode(search),
            relevance=RelevanceNode(provider),
            bodies=BodiesNode(),
            fetch_body=FetchBodyNode(scrape, storage=storage),
            synthesize=SynthesizeNode(provider, storage=storage),
            judge=JudgeNode(provider),
            review=ReviewNode(),
            render=RenderNode(root="knowledge_base"),
        ),
        checkpointer=InMemorySaver(),
    )


async def test_runtime_injects_derived_recursion_limit(monkeypatch) -> None:
    """run() must pass the derived workflow limit into ainvoke's config."""
    captured: dict[str, Any] = {}
    real_ainvoke = CompiledStateGraph.ainvoke

    async def spy(self: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
        captured.update(config or {})
        return await real_ainvoke(self, input, config=config, **kwargs)

    monkeypatch.setattr(CompiledStateGraph, "ainvoke", spy)
    await _runtime().run("research", "t-limit", "multi-agent orchestration")
    assert captured.get("recursion_limit") == DEFAULT_LIMITS["research"]


async def test_override_limit_respected(monkeypatch) -> None:
    """An explicit per-call recursion_limit wins over the derived default."""
    captured: dict[str, Any] = {}
    real_ainvoke = CompiledStateGraph.ainvoke

    async def spy(self: Any, input: Any, config: Any = None, **kwargs: Any) -> Any:
        captured.update(config or {})
        return await real_ainvoke(self, input, config=config, **kwargs)

    monkeypatch.setattr(CompiledStateGraph, "ainvoke", spy)
    await _runtime().run("research", "t-override", "multi-agent orchestration", recursion_limit=999)
    assert captured.get("recursion_limit") == 999


async def test_tiny_limit_stops_ping_pong_graph() -> None:
    """A looping graph with a tiny recursion_limit raises instead of spinning."""

    class Loop(TypedDict, total=False):
        n: int

    def ping(state: Loop) -> Command[Any]:
        return Command(update={"n": state.get("n", 0) + 1}, goto="pong")

    def pong(state: Loop) -> Command[Any]:
        return Command(update={}, goto="ping")

    graph: StateGraph[Any, Any, Any, Any] = StateGraph(Loop)
    graph.add_node("ping", ping)
    graph.add_node("pong", pong)
    graph.add_edge(START, "ping")
    app = graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(GraphRecursionError):
        await app.ainvoke({"n": 0}, config={"recursion_limit": 5, "configurable": {"thread_id": "t-loop"}})

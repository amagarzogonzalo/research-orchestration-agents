"""Settings channel (W6, ADR 0016): per-run overrides without code changes.

Guarantees: overrides flow engine -> initial state -> node reads; defaults
(no settings) keep prompts byte-identical so eval replay stays green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from knowledge_engine.orchestration.graphs import NodeSet
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
from knowledge_engine.orchestration.runtime import GraphRuntime
from knowledge_engine.schemas.decomposition import MAX_SUBQUERIES
from knowledge_engine.schemas.provider import ChatResult, UsageSummary
from knowledge_engine.schemas.settings import RunSettings
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch

_FOUR_PLAN = json.dumps(
    {"sub_queries": [{"query": f"sub {i}", "source": "search", "rationale": "r"} for i in range(4)]}
)


class _FourBranchProvider(FakeProvider):
    """FakeProvider whose planner reply proposes four sub-queries."""

    async def complete(self, req: Any) -> ChatResult:
        """Reply with a four-branch plan for planner prompts.

        Args:
            req: Chat request.

        Returns:
            ChatResult: Fixed four-sub-query JSON for planning, else parent reply.
        """
        if "research planner" in (req.messages[0].content if req.messages else ""):
            usage = UsageSummary(input_tokens=10, output_tokens=10, total_tokens=20)
            return ChatResult.from_response(_FOUR_PLAN, provider="fake", model="fake", usage=usage)
        return await super().complete(req)


def _deep_runtime(tmp_path: Path, search: Any = None, provider: Any = None) -> GraphRuntime:
    """Offline deep-capable runtime (same wiring as test_deep_graph).

    Args:
        tmp_path: Scratch dir.
        search: Optional search fake override.
        provider: Optional provider fake override.

    Returns:
        GraphRuntime: Configured runtime.
    """
    search, provider = search or FakeSearch(), provider or FakeProvider()
    scrape = FakeScrape()
    storage = LocalBodyStorage(root=tmp_path / "bodies")
    ingest, relevance = IngestNode(search), RelevanceNode(provider)
    fetch_body, synthesize = FetchBodyNode(scrape, storage=storage), SynthesizeNode(provider, storage=storage)
    return GraphRuntime(
        nodes=NodeSet(
            ingest=ingest,
            relevance=relevance,
            bodies=BodiesNode(),
            fetch_body=fetch_body,
            synthesize=synthesize,
            judge=JudgeNode(provider),
            review=ReviewNode(),
            render=RenderNode(root=str(tmp_path / "out")),
            intent=IntentNode(provider=provider),
            decompose=DecomposeNode(provider=provider),
            branch=BranchNode(ingest=ingest, relevance=relevance, fetch_body=fetch_body, synthesize=synthesize),
            aggregate=AggregateNode(provider=provider),
            critic=CriticNode(provider=provider),
        ),
        checkpointer=InMemorySaver(),
    )


async def test_max_subqueries_caps_the_plan() -> None:
    """settings.max_subqueries=2 truncates a 4-branch plan at the node."""
    node = DecomposeNode(_FourBranchProvider())
    state: dict[str, Any] = {"query": "q", "depth": 0, "rounds": 0, "settings": {"max_subqueries": 2}}
    update = await node.run(state)  # type: ignore[arg-type]
    assert len(update["sub_queries"]) == 2, "override must truncate the plan"


async def test_no_settings_means_constants() -> None:
    """Without settings the plan is capped by MAX_SUBQUERIES (unchanged behavior)."""
    node = DecomposeNode(_FourBranchProvider())
    state: dict[str, Any] = {"query": "q", "depth": 0, "rounds": 0}
    update = await node.run(state)  # type: ignore[arg-type]
    assert len(update["sub_queries"]) == min(4, MAX_SUBQUERIES)


async def test_runtime_plumbs_settings_channel(tmp_path) -> None:
    """runtime.run(settings=...) reaches nodes; max_depth=0 short-circuits planning."""
    search = FakeSearch()
    await _deep_runtime(tmp_path, search=search).run(
        "deep", "t-set", "state of the art in retrieval augmented generation", settings={"max_depth": 0}
    )
    assert search.calls == 0, "depth cap 0 must stop planning before any search"


async def test_unknown_keys_rejected() -> None:
    """extra=forbid: settings cannot smuggle arbitrary state or model names."""
    with pytest.raises(ValidationError):
        RunSettings(model="gpt-4o")  # type: ignore[call-arg]

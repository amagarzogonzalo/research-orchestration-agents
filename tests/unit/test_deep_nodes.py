"""Unit tests: intent / decompose / branch / aggregate nodes (TDD red, fakes only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.nodes.aggregate import AggregateNode
from knowledge_engine.orchestration.nodes.branch import BranchNode
from knowledge_engine.orchestration.nodes.critic import CriticNode
from knowledge_engine.orchestration.nodes.decompose import DecomposeNode
from knowledge_engine.orchestration.nodes.intent import IntentNode
from knowledge_engine.schemas.provider import ChatRequest, ChatResult
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


class GarbageProvider(FakeProvider):
    """Returns unparsable output for fail-closed tests."""

    async def complete(self, req: ChatRequest) -> ChatResult:
        raw = await super().complete(req)
        return ChatResult.from_response(
            "not json at all {{{",
            provider="fake",
            model="fake",
            usage=raw.usage,
        )


class FencedProvider(FakeProvider):
    """Wraps every reply in markdown fences + prose (audit defect: fenced plans)."""

    async def complete(self, req: ChatRequest) -> ChatResult:
        raw = await super().complete(req)
        return ChatResult.from_response(
            f"Here is the plan:\n```json\n{raw.message.content}\n```\nDone.",
            provider="fake",
            model="fake",
            usage=raw.usage,
        )


class SourceLike:
    """Record double: ``merge_sources`` needs ``.id``, the rubric needs ``.domain``."""

    def __init__(self, sid: str, domain: str) -> None:
        self.id = sid
        self.domain = domain


class ExplodingProvider(ProviderPort):
    """Always fails (fail-open tests)."""

    async def complete(self, req: ChatRequest) -> ChatResult:
        raise RuntimeError("provider down")

    async def extract(self, req: Any) -> object:
        raise RuntimeError("provider down")


async def _nodeset_branch() -> BranchNode:
    import tempfile

    from knowledge_engine.orchestration.nodes import FetchBodyNode, IngestNode, RelevanceNode, SynthesizeNode
    from knowledge_engine.store.body_file_store import LocalBodyStorage

    provider = FakeProvider()
    storage = LocalBodyStorage(root=Path(tempfile.mkdtemp()) / "bodies")
    return BranchNode(
        ingest=IngestNode(FakeSearch()),
        relevance=RelevanceNode(provider),
        fetch_body=FetchBodyNode(FakeScrape(), storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
    )


async def test_intent_node_deterministic_fast_path() -> None:
    node = IntentNode()
    assert (await node.run({"query": "hi"}))["intent"] == "chat"
    assert (await node.run({"query": "what is the capital of France?"}))["intent"] == "quick"


async def test_intent_node_mode_override_wins_without_llm() -> None:
    node = IntentNode(provider=FakeProvider())
    out = await node.run({"query": "hi", "mode": "deep"})
    assert out["intent"] == "deep"


async def test_intent_node_llm_fallback_and_fail_open() -> None:
    node = IntentNode(provider=FakeProvider())
    out = await node.run({"query": "tell me about memory systems in agents"})
    assert out["intent"] == "deep"
    fallback = IntentNode(provider=ExplodingProvider())
    assert (await fallback.run({"query": "tell me about memory systems in agents"}))["intent"] == "deep"


async def test_decompose_node_fans_out_with_budgets() -> None:
    node = DecomposeNode(provider=FakeProvider(), max_subqueries=5, max_depth=2)
    out = await node.run({"query": "compare memory systems", "depth": 0, "rounds": 0, "llm_calls": 0})
    assert len(out["sub_queries"]) == 2
    assert out["depth"] == 1
    assert out["rounds"] == 1
    assert out["llm_calls"] == 1


async def test_decompose_node_depth_guard_skips_llm() -> None:
    provider = FakeProvider()
    node = DecomposeNode(provider=provider, max_depth=2)
    out = await node.run({"query": "q", "depth": 2, "rounds": 1, "llm_calls": 4})
    assert out["sub_queries"] == []
    assert provider.calls == []


async def test_decompose_node_fail_closed_on_garbage() -> None:
    node = DecomposeNode(provider=GarbageProvider())
    out = await node.run({"query": "original question", "depth": 0, "rounds": 0, "llm_calls": 0})
    assert len(out["sub_queries"]) == 1
    assert out["sub_queries"][0]["query"] == "original question"


async def test_decompose_node_parses_fenced_json_plan() -> None:
    """A fenced/prose-wrapped plan still fans out (audit defect: deep fell to one branch)."""
    node = DecomposeNode(provider=FencedProvider(), max_subqueries=5, max_depth=2)
    out = await node.run({"query": "compare memory systems", "depth": 0, "rounds": 0, "llm_calls": 0})
    assert len(out["sub_queries"]) == 2
    assert out["sub_queries"][0]["query"] == "sub query one"


async def test_branch_node_runs_leaf_pipeline_reducer_keys_only() -> None:
    branch = await _nodeset_branch()
    out = await branch.run({"sub_query": {"query": "memory systems", "source": "search", "depth": 1}, "llm_calls": 0})
    assert set(out) <= {"branch_notes", "bodies", "llm_calls", "branch_sources"}
    assert len(out["branch_notes"]) == 1
    assert len(out["bodies"]) >= 1


async def test_branch_node_mcp_source_uses_mcp_port_with_fallback() -> None:
    import tempfile

    from knowledge_engine.orchestration.nodes import FetchBodyNode, IngestNode, RelevanceNode, SynthesizeNode
    from knowledge_engine.store.body_file_store import LocalBodyStorage

    search, mcp_search, provider = FakeSearch(), FakeSearch(), FakeProvider()
    storage = LocalBodyStorage(root=Path(tempfile.mkdtemp()) / "bodies")
    branch = BranchNode(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        fetch_body=FetchBodyNode(FakeScrape(), storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
        mcp_search=mcp_search,
    )
    await branch.run({"sub_query": {"query": "q", "source": "mcp", "depth": 1}, "llm_calls": 0})
    assert mcp_search.calls == 1 and search.calls == 0

    branch_no_mcp = BranchNode(
        ingest=IngestNode(search),
        relevance=RelevanceNode(provider),
        fetch_body=FetchBodyNode(FakeScrape(), storage=storage),
        synthesize=SynthesizeNode(provider, storage=storage),
    )
    await branch_no_mcp.run({"sub_query": {"query": "q", "source": "mcp", "depth": 1}, "llm_calls": 0})
    assert search.calls == 1


async def test_aggregate_node_merges_branch_notes() -> None:
    node = AggregateNode(provider=FakeProvider())
    out = await node.run({"query": "q", "branch_notes": ["note A", "note B"]})
    assert len(out["notes"]) == 1
    assert (await node.run({"query": "q", "branch_notes": []}))["notes"] == []


async def test_aggregate_rubric_sees_branch_sources() -> None:
    """Deep rubric diversity reads branch_sources (audit defect: empty sources channel)."""
    node = AggregateNode(provider=FakeProvider())
    out = await node.run(
        {
            "query": "q",
            "branch_notes": ["note A", "note B"],
            "branch_sources": [SourceLike("s1", "a.org"), SourceLike("s2", "b.org")],
        }
    )
    assert out["rubric_score"] >= 0.6
    assert not any(gap.startswith("diversity:") for gap in out["gaps"])


async def test_critic_node_structured_feedback_extends_gaps() -> None:
    node = CriticNode(provider=FakeProvider())
    out = await node.run({"query": "q", "notes": ["finding"], "gaps": ["coverage: only 0 bodies (need 3)"]})
    assert out["critic_report"]["verdict"] == "sufficient"
    assert "coverage: only 0 bodies (need 3)" in out["gaps"]
    assert out["llm_calls"] == 1


async def test_critic_node_falls_back_deterministically() -> None:
    for provider in (GarbageProvider(), ExplodingProvider()):
        node = CriticNode(provider=provider)
        out = await node.run({"query": "q", "branch_notes": [], "gaps": []})
        assert out["critic_report"]["verdict"] == "replan"
        assert out["gaps"]
        assert out["llm_calls"] == 1


async def test_critic_fallback_sees_branch_sources() -> None:
    """Deterministic critic fallback reads branch_sources for diversity."""
    node = CriticNode(provider=ExplodingProvider())
    out = await node.run(
        {
            "query": "q",
            "branch_notes": ["note A", "note B"],
            "branch_sources": [SourceLike("s1", "a.org"), SourceLike("s2", "b.org")],
            "gaps": [],
        }
    )
    assert not any("no source domains" in gap for gap in out["gaps"])

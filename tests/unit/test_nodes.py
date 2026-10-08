"""Unit tests: ingest / relevance / bodies / synthesize / judge / review / render nodes."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes import (
    BodiesNode,
    FetchBodyNode,
    IngestNode,
    JudgeNode,
    RelevanceNode,
    RenderNode,
    ReviewNode,
    SynthesizeNode,
    judge_route,
    review_route,
)
from knowledge_engine.orchestration.nodes.bodies import FETCH_NODE
from knowledge_engine.orchestration.nodes.relevance import _parse_relevant_ids
from knowledge_engine.schemas.provider import ChatResult, UsageSummary
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeEmptySearch, FakeProvider, FakeScrape, FakeSearch


async def _state(**overrides) -> dict:
    base = {"thread_id": "t1", "query": "agents", "notes": [], "iterations": 0}
    base.update(overrides)
    return base


async def test_ingest_writes_sources() -> None:
    search = FakeSearch()
    node = IngestNode(search)
    out = await node.run(await _state())
    assert out["sources"][0].id == "openalex:W1"
    assert out["status"] == "running"


async def test_ingest_handles_empty_results() -> None:
    node = IngestNode(FakeEmptySearch())
    out = await node.run(await _state())
    assert out["sources"] == []


async def test_relevance_filters_with_json_ids() -> None:
    keep = SourceRecord(id="keep1", title="attention survey", source=SearchSource.OPENALEX)
    drop = SourceRecord(id="drop1", title="unrelated medical note", source=SearchSource.OPENALEX)

    class SelectiveProvider(FakeProvider):
        async def complete(self, req):
            self.calls.append(req.affinity.value)
            return ChatResult.from_response(
                '{"relevant_ids": ["keep1"]}',
                provider="fake",
                model="fake",
                usage=UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=0.0),
            )

    out = await RelevanceNode(SelectiveProvider()).run(await _state(sources=[keep, drop]))
    assert out["filtered"] == [keep]


async def test_relevance_degrades_to_keep_all_on_failure() -> None:
    class BoomProvider(FakeProvider):
        async def complete(self, req):
            raise RuntimeError("network down")

    src = SourceRecord(id="s1", title="t", source=SearchSource.OPENALEX)
    out = await RelevanceNode(BoomProvider()).run(await _state(sources=[src]))
    # degrade: keep everything so the judge can still score
    assert out["filtered"] == [src]


async def test_relevance_keep_all_when_reply_unparseable() -> None:
    class GarbledProvider(FakeProvider):
        async def complete(self, req):
            return ChatResult.from_response(
                "I think maybe these are relevant",
                provider="fake",
                model="fake",
                usage=UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=0.0),
            )

    src = SourceRecord(id="s1", title="t", source=SearchSource.OPENALEX)
    out = await RelevanceNode(GarbledProvider()).run(await _state(sources=[src]))
    assert out["filtered"] == [src]  # unparseable -> keep-all degrade


async def test_relevance_parses_id_in_json() -> None:
    assert _parse_relevant_ids('```json\n{"relevant_ids": ["a", "b"]}\n```') == ["a", "b"]
    assert _parse_relevant_ids('{"ids": ["x"]}') == ["x"]
    assert _parse_relevant_ids("not json") is None


async def test_relevance_ledger_carries_authority_signals() -> None:
    seen: list[str] = []

    class CaptureProvider(FakeProvider):
        async def complete(self, req):
            seen.append(req.messages[-1].content)
            return await super().complete(req)

    src = SourceRecord(
        id="s1",
        title="t",
        source=SearchSource.OPENALEX,
        venue="Nature",
        year=2024,
        citation_count=13235,
    )
    await RelevanceNode(CaptureProvider()).run(await _state(sources=[src]))
    ledger = seen[0]
    # Slim ledger contract: id + title + venue (authority signal). Year and
    # cite counts are dropped on purpose to keep reasoning-model verdicts
    # inside the token budget.
    assert "Nature" in ledger and "s1" in ledger
    assert "2024" not in ledger and "13235" not in ledger


async def test_bodies_fanout_emits_send_per_source() -> None:
    out = await BodiesNode().run(await _state(filtered=[{"id": "a"}, {"id": "b"}]))
    assert hasattr(out, "goto")  # Command(goto=[Send, ...])
    sends = out.goto
    assert isinstance(sends, list) and len(sends) == 2
    assert all(getattr(s, "node", None) == FETCH_NODE for s in sends)


async def test_bodies_empty_filters_routes_to_join() -> None:
    node = BodiesNode()
    out = await node.run(await _state(filtered=[]))
    assert getattr(out, "goto", None) == "synthesize"


async def test_fetch_body_scrapes_source(fake_scrape, tmp_path) -> None:
    src = SourceRecord(id="arxiv:2301.00001", title="paper", source=SearchSource.ARXIV)
    node = FetchBodyNode(fake_scrape, storage=LocalBodyStorage(root=tmp_path / "bodies"))
    out = await node.run({"source": src})
    ref = out["bodies"][0]
    assert ref.url == "https://arxiv.org/abs/2301.00001"
    assert fake_scrape.calls == ["https://arxiv.org/abs/2301.00001"]
    assert ref.char_count > 0, "the ref points at persisted markdown"


async def test_fetch_body_skips_failed_url(tmp_path) -> None:
    node = FetchBodyNode(FakeScrape(fail_urls={"https://bad"}), storage=LocalBodyStorage(root=tmp_path / "bodies"))
    out = await node.run(
        {"source": SourceRecord(id="x", title="t", source=SearchSource.OPENALEX, pdf_url="https://bad")}
    )
    assert out["bodies"] == []


async def test_synthesize_appends_notes_and_bumps_iterations(fake_provider, tmp_path) -> None:
    node = SynthesizeNode(fake_provider, storage=LocalBodyStorage(root=tmp_path / "bodies"))
    out = await node.run(await _state(bodies=[{"url": "u", "title": "t", "markdown": "content"}]))
    assert out["notes"] == ["synthesis v1"]
    assert out["iterations"] == 1


async def test_judge_sets_score_and_route(fake_provider) -> None:
    node = JudgeNode(fake_provider)
    # The fake provider detects a judge prompt via "Score groundness" content.
    out = await node.run(await _state(notes=['Score groundedness 0.9 {"score": 0.9, "pass": true, "critique": ""}']))
    assert out["judge_score"] == 0.9
    assert out["judge_critique"] == ""
    assert judge_route(out) == "review"


def test_judge_route_iterates_within_budget() -> None:
    assert judge_route({"judge_critique": "fix it", "iterations": 0, "notes": ["x"]}) == "synthesize"
    assert judge_route({"judge_critique": "fix it", "iterations": 2, "notes": ["x", "y"]}) == "review"
    assert judge_route({"judge_critique": "", "iterations": 0}) == "review"


async def test_review_auto_approves() -> None:
    out = await ReviewNode().run(await _state(notes=["draft"]))
    assert out["review_dir"] == "approved"
    assert review_route(out) == "render"


async def test_review_rejects_when_gate_says_no() -> None:
    async def reject(query, draft):
        return {"approved": False, "feedback": "rewrite"}

    out = await ReviewNode(gate=reject).run(await _state(notes=["draft"]))
    assert out["review_dir"] == "rejected"
    assert review_route(out) == "__end__"


async def test_render_writes_artifact(tmp_path) -> None:
    root = str(tmp_path / "kb")
    out = await RenderNode(root=root).run(await _state(notes=["final notes"]))
    assert out["status"] == "done"
    assert out["output_path"].endswith("article.md")
    import pathlib

    assert pathlib.Path(out["output_path"]).exists()
    text = pathlib.Path(out["output_path"]).read_text(encoding="utf-8")
    assert "final notes" in text
    assert "frontmatter" in text or "@id" in text

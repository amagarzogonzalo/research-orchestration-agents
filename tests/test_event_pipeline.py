"""End-to-end event choreography tests (ADR 0007). Uses FakeBroker + fake ports."""

from __future__ import annotations

from knowledge_engine.infrastructure.broker import Envelope
from knowledge_engine.orchestration.event_pipeline import EventPipeline, decode_state
from knowledge_engine.orchestration.stations import default_stations
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult, UsageSummary
from knowledge_engine.schemas.source import DocumentBody, SearchSource, SourceRecord
from tests.fake_broker import FakeBroker


class FakeSearch:
    async def search(self, query, *, limit=10):
        return [
            SourceRecord(
                id="openalex:W1",
                title=f"{query} alpha",
                source=SearchSource.OPENALEX,
                pdf_url="https://example.org/paper.pdf",
            )
        ]


class FakeScrape:
    async def to_markdown(self, url):
        return DocumentBody(url=url, title="body", markdown="LLM-ready markdown content.")


class FakeProvider:
    """Pluggable fake LLM: content depends on the task prompt."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, req: ChatRequest) -> ChatResult:
        self.calls.append(req.affinity.value)
        user = req.messages[-1].content
        if req.affinity == Affinity.MICRO:
            text = "true"
        elif "Reviewer feedback to address" in user:
            text = "synthesis v2"
        elif "Score groundedness" in user or "groundedness" in user:
            text = '{"score": 0.9, "pass": true, "critique": ""}'
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


def build_pipeline(*, auto_approve_review=True) -> tuple[EventPipeline, FakeBroker, FakeProvider]:
    broker = FakeBroker()
    provider = FakeProvider()
    pipeline = EventPipeline(
        broker=broker,
        stations=default_stations(),
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=provider,
        auto_approve_review=auto_approve_review,
    )
    return pipeline, broker, provider


async def test_full_choreography_completes() -> None:
    pipeline, broker, provider = build_pipeline()
    await pipeline.submit(thread_id="T1", query="multi-agent orchestration")
    counts = await broker.run_cycle(pipeline.handler_map())
    completed = broker.drain("research.completed")
    assert completed, "expected a completed event"
    state = decode_state(Envelope.from_dict(completed[-1]))
    assert state.judge_score is not None
    assert state.synthetic_notes
    # every station's inbox was consumed at least once on the happy path
    for stream in ("research.inbox", "research.sources.resolved", "research.sources.filtered",
                   "research.bodies.ready", "research.judged", "research.review", "research.render.request"):
        assert counts.get(stream, 0) >= 1, f"{stream} was not consumed"


async def test_state_flows_with_enrichment() -> None:
    pipeline, broker, provider = build_pipeline()
    await pipeline.submit(thread_id="T2", query="event driven agents")
    await broker.run_cycle(pipeline.handler_map())
    completed = decode_state(Envelope.from_dict(broker.drain("research.completed")[-1]))
    assert completed.sources, "sources should be populated by ingest"
    assert completed.filtered, "relevance should filter"
    assert completed.bodies, "bodies should be fetched"
    assert "alpha" in completed.sources[0].title


async def test_review_gate_blocks_when_disabled() -> None:
    pipeline, broker, provider = build_pipeline(auto_approve_review=False)
    await pipeline.submit(thread_id="T3", query="human in loop")
    await broker.run_cycle(pipeline.handler_map())
    render = broker.drain("research.render.request")
    assert not render, "render should NOT happen without human approval"
    # find the review.requested event's carried state from processed history
    requested = next(e for e in broker.history if e.type == "review.requested")
    state = decode_state(requested)
    # external reviewer approves -> render proceeds
    await pipeline.emit_new(
        "research.review",
        "review.approved",
        {"state": state.model_dump(), "thread_id": "T3"},
        correlation_id="T3",
    )
    await broker.run_cycle(pipeline.handler_map())
    assert broker.drain("research.completed"), "saga should complete after human approval"


async def test_idempotent_replay_does_not_duplicate() -> None:
    pipeline, broker, provider = build_pipeline()
    await pipeline.submit(thread_id="T4", query="dedupe check")
    await broker.run_cycle(pipeline.handler_map())
    before = len(provider.calls)
    # Re-running the cycle must not re-process already-handled events.
    await broker.run_cycle(pipeline.handler_map())
    assert len(provider.calls) == before


async def test_terminal_error_emitted_on_station_crash() -> None:
    broker = FakeBroker()

    class BoomStation:
        name = "boom"
        in_stream = "research.inbox"

        async def handle(self, pipeline, envelope):
            raise RuntimeError("kaboom")

    pipeline = EventPipeline(broker=broker, stations=[BoomStation()])
    await pipeline.submit(thread_id="T5", query="x")
    await broker.run_cycle(pipeline.handler_map())
    errs = broker.drain("research.errored")
    assert errs and "kaboom" in (errs[-1].get("payload") or {}).get("error", "")

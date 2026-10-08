"""Span coverage (W5, ADR 0015): a full run traces every node with thread correlation.

The tracer is patched into an in-memory exporter; assertions are on span NAMES
and attributes, never on state contents (PII rule). The no-op default (exporter
unset) is untouched in production paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from knowledge_engine.observability import telemetry
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
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape, FakeSearch

_EXPECTED_NODES = {
    "node.IngestNode",
    "node.RelevanceNode",
    "node.BodiesNode",
    "node.FetchBodyNode",
    "node.SynthesizeNode",
    "node.JudgeNode",
    "node.ReviewNode",
    "node.RenderNode",
}


@pytest.fixture
def span_recorder(monkeypatch) -> InMemorySpanExporter:
    """Route the engine tracer into an in-memory exporter for the test.

    Args:
        monkeypatch: Pytest patching fixture.

    Returns:
        InMemorySpanExporter: Captured finished spans.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    return exporter


class _RichFakeScrape(FakeScrape):
    """FakeScrape padded past the router's _MIN_CHARS content-quality guard."""

    async def to_markdown(self, url: str):
        body = await super().to_markdown(url)
        body.markdown = body.markdown + "evidence " * 60
        return body


def _runtime(tmp_path: Path, search=None, scrape=None) -> GraphRuntime:
    """Build an offline runtime: fake backends wrapped in the real adapters.

    Args:
        tmp_path: Scratch dir for bodies + artifacts.
        search: Optional inner search fake override.
        scrape: Optional inner scrape fake override.

    Returns:
        GraphRuntime: Configured runtime.
    """
    from knowledge_engine.infrastructure.scrape.scrape_router import ScrapeRouter
    from knowledge_engine.infrastructure.search.demux import DemuxSearchAdapter

    inner_search, inner_scrape, provider = search or FakeSearch(), scrape or _RichFakeScrape(), FakeProvider()
    real_search = DemuxSearchAdapter([inner_search])
    real_scrape = ScrapeRouter(chain=[inner_scrape])
    storage = LocalBodyStorage(root=tmp_path / "bodies")
    return GraphRuntime(
        nodes=NodeSet(
            ingest=IngestNode(real_search),
            relevance=RelevanceNode(provider),
            bodies=BodiesNode(),
            fetch_body=FetchBodyNode(real_scrape, storage=storage),
            synthesize=SynthesizeNode(provider, storage=storage),
            judge=JudgeNode(provider),
            review=ReviewNode(),
            render=RenderNode(root=str(tmp_path / "out")),
        ),
        checkpointer=InMemorySaver(),
    )


async def test_research_run_traces_every_node(span_recorder, tmp_path) -> None:
    """A full research run emits one span per executed node, thread-correlated."""
    await _runtime(tmp_path).run("research", "t-span", "multi-agent orchestration")
    spans = span_recorder.get_finished_spans()
    names = {span.name for span in spans}
    assert _EXPECTED_NODES <= names
    correlated = [span for span in spans if (span.attributes or {}).get("thread.id") == "t-span"]
    assert len(correlated) >= 7, "node spans carry thread.id"


async def test_adapter_spans_carry_query_and_result_counts(span_recorder, tmp_path) -> None:
    """Federated search + scrape spans expose query/url plus result sizes."""
    await _runtime(tmp_path).run("research", "t-span2", "multi-agent orchestration")
    spans = span_recorder.get_finished_spans()
    search_spans = [s for s in spans if s.name == "search.federated"]
    assert search_spans, "demux search must emit a span"
    attrs = search_spans[0].attributes or {}
    assert attrs.get("query") == "multi-agent orchestration"
    assert int(attrs.get("sources_found", 0)) >= 1
    scrape_spans = [s for s in spans if s.name == "scrape.ScrapeRouter"]
    assert scrape_spans, "scrape router must emit a span"
    assert "chars" in (scrape_spans[0].attributes or {})


def test_span_attribute_coercion() -> None:
    """Non-scalar attribute values are stringified; None is dropped."""
    with telemetry.span("probe", thread="t", count=3, obj={"a": 1}, nothing=None) as current:
        attrs = current.attributes or {}
    assert attrs.get("count") == 3
    assert attrs.get("obj") == "{'a': 1}"
    assert "nothing" not in attrs

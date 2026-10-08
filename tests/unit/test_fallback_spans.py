"""Fallback-chain observability (issue #21): per-provider spans + outcome counters.

Every provider attempt in the general-web chain emits a span with an
``outcome`` attribute (success/empty/failure/quota/breaker) and increments a
counter — quality gaps localize instantly instead of hiding in log lines.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from knowledge_engine.infrastructure.search.fallback import FallbackSearchAdapter
from knowledge_engine.observability import telemetry
from knowledge_engine.schemas.source import SourceRecord
from tests.conftest import FakeSearch


def _record(url: str) -> SourceRecord:
    return SourceRecord(id=f"duckduckgo:{url}", title=url, source="duckduckgo")


@pytest.fixture
def span_recorder(monkeypatch) -> InMemorySpanExporter:
    """Route the engine tracer into an in-memory exporter.

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


@pytest.fixture
def counter_spy(monkeypatch):
    """Replace the search-provider counter with a recording spy.

    Args:
        monkeypatch: Pytest patching fixture.

    Returns:
        list[tuple[float, dict[str, str]]]: Recorded (amount, attributes).
    """
    recorded: list[tuple[float, dict[str, str]]] = []

    class _SpyCounter:
        def add(self, amount: float, attributes: dict[str, str] | None = None) -> None:
            recorded.append((amount, attributes or {}))

    monkeypatch.setattr(telemetry, "search_provider_calls", _SpyCounter())
    return recorded


async def test_success_provider_spans_outcome_and_counts(span_recorder, counter_spy) -> None:
    """A hit provider emits success spans + counter with provider/outcome attrs."""

    class HitSearch(FakeSearch):
        name = "hit"

        async def search(self, query: str, *, limit: int = 10):
            return [_record("https://hit.example/a"), _record("https://hit.example/b")]

    class MissSearch(FakeSearch):
        name = "miss"

        async def search(self, query: str, *, limit: int = 10):
            return []

    chain = FallbackSearchAdapter([MissSearch(), HitSearch()])
    records = await chain.search("q", limit=5)
    assert len(records) == 2, "first provider empty -> escalate; second wins"
    spans = {s.name: (s.attributes or {}) for s in span_recorder.get_finished_spans()}
    assert "search.miss" in spans and spans["search.miss"].get("outcome") == "empty"
    assert "search.hit" in spans and spans["search.hit"].get("outcome") == "success"
    outcomes = [(a.get("provider"), a.get("outcome")) for _, a in counter_spy]
    assert ("miss", "empty") in outcomes
    assert ("hit", "success") in outcomes


async def test_failure_provider_records_failure_outcome(span_recorder, counter_spy) -> None:
    """A raising provider emits outcome=failure (no crash: chain continues)."""

    class BoomSearch(FakeSearch):
        name = "boom"

        async def search(self, query: str, *, limit: int = 10):
            raise RuntimeError("boom")

    class HitSearch(FakeSearch):
        name = "safe"

        async def search(self, query: str, *, limit: int = 10):
            return [_record("https://safe.example/a")]

    chain = FallbackSearchAdapter([BoomSearch(), HitSearch()])
    records = await chain.search("q", limit=5)
    assert len(records) == 1
    outcomes = [(a.get("provider"), a.get("outcome")) for _, a in counter_spy]
    assert ("boom", "failure") in outcomes
    assert ("safe", "success") in outcomes

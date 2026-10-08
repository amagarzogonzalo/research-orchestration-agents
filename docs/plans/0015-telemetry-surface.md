---
@id: "plans/0015"
type: ADR
status: ACCEPTED
date: 2026-10-07
related:
  - 0006   # OpenTelemetry observability (this extends its surface)
  - 0008   # LangGraph orchestrator
---

# 0015 — Telemetry surface: per-node + adapter spans with thread correlation

## Context

ADR 0006 wired the OTLP/metrics plumbing, but only two places emitted signal:
`router.complete` (one span) and the provider adapters (token/duration
metrics). Node execution was invisible: a deep run's 20-minute stall could
not be localized. The layering also forbade `orchestration -> observability`,
making the natural instrumentation point (`WorkflowNode.__call__`) illegal.

## Decision

1. `telemetry.span(name, **attrs)` — a single contextmanager helpers module;
   callers NEVER import an exporter (DIP). `None` attributes drop;
   non-scalars stringify (OTEL accepts str/bool/int/float only).
2. `WorkflowNode.__call__` wraps every node in
   `span("node.<ClassName>", thread.id=...)` + `duration_ms` +
   `updated_keys` (count only — state contents never enter attributes, PII
   rule). Exceptions are recorded on the span, then re-raised.
3. Adapter surfaces emit one span each: `search.federated` (query,
   sources_found) and `scrape.ScrapeRouter` (url, chars, scraper).
4. Layering amendment (this ADR (0015) records it): `orchestration`,
   `infrastructure.search`, and `infrastructure.scrape` now depend on
   `observability` — a leaf module (depends_on nothing), same direction as
   the existing `infrastructure -> observability` edge. No cycle introduced.
5. The env-gated no-op default (`OTEL_TRACES_EXPORTER` unset) is unchanged:
   CI stays offline, span cost is a context manager around a no-op tracer.

## Consequences

- **Positive:** every run is traceable end-to-end (nodes + adapters +
  router), thread-correlatable for multi-run debugging; test surface:
  `tests/unit/test_span_coverage.py` (in-memory exporter, no new dependency).
- **Negative:** one extra span object per node execution (negligible at
  current scale; re-evaluate if fan-out grows past ~50 Sends).
- **Zero-cost:** OTLP export stays off by default; a local collector is the
  operator's choice.

## References

- `observability/telemetry.py` — span helper.
- `orchestration/state.py` — WorkflowNode wrap.
- `tests/unit/test_span_coverage.py` — coverage assertions.

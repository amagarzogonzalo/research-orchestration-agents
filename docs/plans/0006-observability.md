---
@id: "plans/0006"
type: ADR
status: ACCEPTED
date: 2026-09-03
related:
  - 0004
  - 0005
---

# 0006 — OpenTelemetry GenAI Observability (Zero-Cost)

## Context

A parallelized, multi-provider orchestrator-worker graph cannot be debugged via log lines. We need token counts, latency, cost, and circuit-breaker trips traceable across distributed nodes — **and it must cost $0**. Datadog and LangSmith are explicitly out of scope.

## Decision

Instrument day one with `opentelemetry-sdk==1.44.x` using the **GenAI semantic conventions** (trace spans + metrics for model/token/cost), exporting to a free self-hosted telemetry sink:

- **Exporter**: OTLP JSON to a filesystem endpoint OR a small self-hosted Collector on a free Compute/Cloud Run instance. Primary MVP: `OTLPSpanExporter` -> append-only JSONL under a mounted volume.
- **Dashboarding**: a lightweight OSS tool (e.g., `pastel`/CLI dashboards) reading the JSONL; no SaaS, no cost.
- **Fallback**: if honest traces are needed in prod without a collector, ship metrics to a Redis counter + a minimal `/metrics` Prometheus scrape endpoint on a free Compute instance.

## Spans / Attributes (GenAI semantic-convention aligned)

On every provider call (in `FallbackRouter.complete`, `0004`):

- `gen_ai.system`, `gen_ai.request.model`, `gen_ai.operation.name` (`chat`/`embed`/`extract`).
- `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`.
- `gen_ai.request.temperature`, `gen_ai.request.max_tokens`.
- Custom: `provider`, `affinity`, `circuit_breaker_state`, `http.status_code`, `latency_ms`, `cost_usd`.

## Metric cardinality

- `gen_ai.client.token_usage` (counter, by `provider`+`model`+`operation`)
- `gen_ai.client.operation.duration` (histogram)
- `router.circuit_breaker.trips` (counter), `router.fallback.attempts` (counter, by `affinity`)
- `bucket.waits.queue_depth` (gauge) from `0003`

```python
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, OTLPSpanExporter

tracer_provider = TracerProvider()
tracer_provider.add_span_processor(
    SimpleSpanProcessor(OTLPSpanExporter(endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]))
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("knowledge.router")
```

Wrap one call:

```python
with tracer.start_as_current_span("provider.complete") as span:
    span.set_attribute("gen_ai.request.model", req.affinity)  # + full gen_ai attrs
    res = await adapter.complete(req)
    span.set_attribute("gen_ai.usage.input_tokens", res.usage.input_tokens)
    span.set_attribute("cost_usd", res.usage.cost_usd)
    return res
```

## Guardrail: token accounting is our own

Never trust vendor-reported cost; recompute `cost_usd` against a local `pricing.json` table per provider/model (free tiers report "0", we still record `input/output` counts) so the SRE view is internally consistent and auditable.

## Correlating traces to state

Carry `thread_id` (from `0002.GraphState`) as a span attribute on every span, so operations on one research run are reconstructable end-to-end from the Neon checkpointer (the state of truth) + JSONL traces (the behavior log).

## References
- `docs/instruction.md` §Observability Matrix; `docs/information.md` observability-as-core-tenet.
- OTel semantic conventions `gen_ai.*`; OSS dashboards/prom regardless of SaaS fee. Pin `opentelemetry-sdk==1.44.x`.

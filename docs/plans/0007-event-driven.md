---
@id: "plans/0007"
type: ADR
status: ACCEPTED
date: 2026-09-03
supersedes:
  - 0005   # 0005's synchronous graph is subsumed by event choreography
related:
  - 0001
  - 0002
---

# 0007 — Event-Driven Choreography Over the Shell

## Context

ADR 0005 originally encoded the research curation workflow as a **synchronous
LangGraph graph**: `Pipeline.run()` awaited every port in-process. That is
*workflow-engine* execution, not *event-driven* processing — the Redis Streams
shell (0001) was plumbed in but never actually moved messages between steps. This
violates the blueprint's core thesis: "decouple task ingestion from synchronous
failure points" and treat unavailable endpoints as degraded, asynchronous events.

## Decision

Rearchitect the runtime to **event choreography**. There is no single driver that
waits on the full pipeline; instead the pipeline is a set of independent
**stations**, each an idempotent, replayable handler that:

1. consumes its **inbox stream**,
2. reads the saga `GraphState` carried in the event payload,
3. does one bounded unit of work against the ports (search / scrape / provider),
4. emits successor events (its **outbox**) **before** acknowledging the source
   message (outbox-before-XACK), and
5. snapshots durable saga state per `thread_id` (checkpoint).

LangGraph's role narrows to explicitly defining *which transitions are legal* and
providing durable checkpointing (Postgres on Neon) for pause/resume + HITL; the
*shell* moves the messages. Stations remain shardable/scaleable independently —
e.g. run `bodies` and `provider` stations in separate workers.

## Event topology

| Station | Inbox stream | Outbox stream(s) | Payload |
|---------|--------------|------------------|---------|
| `ingest` | `research.inbox` | `research.sources.resolved` | `GraphState.sources` |
| `relevance` | `research.sources.resolved` | `research.sources.filtered` | `GraphState.filtered` |
| `bodies` | `research.sources.filtered` | `research.bodies.ready` | `GraphState.bodies` (bounded fan-out) |
| `synthesize` | `research.bodies.ready` | `research.judged` (≤N retries) | `GraphState.synthetic_notes` |
| `judge` | `research.judged` | `research.review` **or** `research.bodies.ready` (optimizer loop) | `score`/`critique` |
| `review` | `research.review` | `research.render.request` (on approve) | awaits human `review.approved` |
| `render` | `research.render.request` | `research.completed` | final `GraphState` |

`research.errored` is the terminal-error stream; `research.completed` the saga end.

## Resilience properties (what makes it "improved")

- **Idempotency / dedupe**: every envelope carries `event_id`; a processed-id
  guard makes the broker's requeue/dispatch discipline safe. Same event → same
  outbox, so replay never double-processes (see tests: provider call count stable
  across replays).
- **Outbox-before-XACK**: publish successors then acknowledge; a crash between
  the two re-runs the station, but dedupe makes that idempotent. No lost
  side-effects.
- **Correlation**: all events in one saga share `correlation_id` (thread_id),
  enabling end-to-end tracing (0006).
- **Human-in-the-loop**: `review` station can gate rendering until an external
  `review.approved` event lands (no persistent network connection required; the
  saga *waits*, resumed later).
- **Bounded optimizer**: `judge` loops back to synthesis at most
  `MAX_EVAL_ITERATIONS=2`, then best-effort proceeds — no unbounded loops.
- **Graceful degradation**: a failed station emits a terminal `errored` event
  rather than cascading a synchronous exception.

## Example (test-verified flow)

```python
pipeline = EventPipeline(broker=FakeBroker(), stations=default_stations(),
                         search=..., scrape=..., provider=...)
await pipeline.submit(thread_id="T1", query="multi-agent orchestration")
await broker.run_cycle(pipeline.handler_map())   # ingest→relevance→bodies→synthesize→judge→review→render
# Terminal: research.completed carries a fully-enriched GraphState.
```

## Key files

- `core/events.py` — stream/event-type constants, `EventOut`.
- `infrastructure/broker.py` — `poll_forever`, `consume_group` w/ dedupe + XACK.
- `orchestration/event_pipeline.py` — `EventPipeline` (emit/checkpoint/handler_map).
- `orchestration/stations.py` — the station handlers.
- `worker.py` — `--submit` seeds the first event; `--consume` runs the loop.

## Consequences

- **Cost**: stations are generic over the ports; adding a step = one station +
  two streams, zero core churn (same Hexagonal win as 0001).
- **Testability**: fully offline-testable against an in-memory broker double
  (13 tests, no network) — the resilience semantics are provable without Redis.
- **Trade-off**: envelope payloads carry a full `GraphState` snapshot, so
  high-fan-out sagas carry some redundancy; acceptable for research-scale volumes
  and buys replayability. For very large corpora, swap payload-state for a saga-id
  + Redis hash lookup (the `checkpoint()` already snapshots per thread).

## References
- `docs/instruction.md` §Event-Driven & Hexagonal Architecture, §Agent Orchestration.
- ADR 0001 (ports/shell), ADR 0006 (correlation tracing).
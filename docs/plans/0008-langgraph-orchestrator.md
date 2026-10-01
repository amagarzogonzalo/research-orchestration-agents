---
@id: "plans/0008"
type: ADR
status: ACCEPTED
date: 2026-09-03
supersedes:
  - 0005   # legacy synchronous LangGraph graph (duplicated node logic)
  - 0007   # hand-rolled Redis Streams event choreography
related:
  - 0001   # hexagonal ports/adapters (kept: boundary discipline)
  - 0002   # pydantic schemas
  - 0006   # OpenTelemetry observability
---

# 0008 — LangGraph Orchestrator: Internally Event-Driven, Minimal External Surface

## Context

The repo accumulated **two competing orchestrators**: a legacy synchronous
LangGraph graph (`pipeline.py`, ADR 0005) with duplicated node logic and a dead
`Send("fetch_bodies_thunk", ...)`, and a hand-rolled Redis Streams event
choreography (`event_pipeline.py` + `stations.py`, ADR 0007). Both implement the
same six steps (ingest → relevance → bodies → synthesize → judge → review →
render) in different runtimes, and both carry vestigial wiring (an unused
`BrokerPort`; a `checkpointer` argument that is stored but never used).

Separately, the blueprint's event-driven thesis needs reconciling with the 2026
reality of LangGraph (verified against official docs and the `langchain-ai/langgraph`
repo on 2026-09-03): LangGraph **is** internally event-driven (Pregel supersteps,
`Send`, `Command`, `interrupt`, `astream_events`) and provides durable
checkpointing — the crash-recovery mechanism a broker would otherwise exist for.

## Decision

1. **LangGraph is the orchestrator and its events are internal.** All workflow
   events (sources resolved, relevance, bodies, judged, review, render) stay
   inside the graph: Pregel superstep events, `Send` fan-out, `Command(goto=...)`
   routing, and `interrupt()` for the optional HITL review gate. Nothing is
   republished to any broker.

2. **External event surface is minimal and synchronous:** one async entrypoint
   `GraphRuntime.run(workflow, thread_id, query) -> GraphState`. No Redis Streams,
   no consumer groups, no XACK, no token-bucket broker, no Postgres/SQLite by
   default.

3. **The checkpointer is the only persistence abstraction** — a one-function
   factory `build_checkpointer(kind)` defaulting to `InMemorySaver`
   (`langgraph.checkpoint.memory`), with the durable option being our
   files-only `FileCheckpointer` (JSON under `research_store/checkpoints/`,
   ADR 0009). No ORM, no repository layer.

4. **Node classes, one per file, SOLID.** `orchestration/state.py` defines the
   typed `WorkflowState` (TypedDict + `Annotated[list, add]` reducer) and the
   `WorkflowNode` base (`async run(state) -> dict`); `orchestration/nodes/` has
   one file per node (`ingest`, `relevance`, `bodies`, `synthesize`, `judge`,
   `review`, `render`). Nodes are immutable, dependency-injected, and stateless
   per run.

5. **Multiple workflows = multiple graphs** via `WorkflowRegistry`
   (`orchestration/graphs.py`): `research`, `qa`, `summary` reusing the same node
   classes. Each graph is built with `StateGraph`, graph-wide
   `set_node_defaults(retry_policy=..., timeout=..., error_handler=...)`, per-node
   `error_handler -> Command(goto=...)` (Saga compensation), `Send` fan-out for
   bodies, and `add_conditional_edges` for the judge→synthesize optimizer loop.

6. **MCP is the tool layer** (`infrastructure/mcp/`): FastMCP `search_server.py`
   + `scrape_server.py` wrapping the existing adapters, loaded into nodes via
   `langchain-mcp-adapters.load_mcp_tools`. Deterministic port-based fallback keeps
   unit/integration tests offline (no live MCP server or LLM).

7. **Deletions:** `orchestration/pipeline.py`, `event_pipeline.py`, `stations.py`,
   `core/events.py`, `infrastructure/broker.py`, and the EDA tests
   (`tests/test_event_pipeline.py`, `tests/fake_broker.py`).

## Consequences

- **Positive:** one orchestrator, no duplicated step logic; SOTA LangGraph
  primitives (retry/timeout/saga/HITL/fan-out) instead of hand-rolled fallback;
  durability via checkpointer; multiple workflows as multiple graphs; fully
  offline-testable (fakes + `InMemorySaver`).
- **Negative:** the old station/event code is removed (the HITL review gate is
  re-expressed via `interrupt()`); persistence swap requires the factory call to
  change (one line in the composition root).
- **Zero-cost:** files-only checkpointer costs nothing now and moves with the
  data (ADR 0009); a durable store is just the `research_store/` folder.

## References

- `docs/langgraph/architecture.md` — full SOTA analysis + module map.
- ADR `0009` — portable research library: files-only persistence + browser + free-LLM discovery (extends this decision).
- Official docs: `application-structure`, `thinking-in-langgraph`, `graph-api`,
  `fault-tolerance`, `checkpointers`, `interrupts`, `event-streaming`.
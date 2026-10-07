# LangGraph Architecture & Best Practices

> Written from live-verified primary sources (2026-09-03): the official
> LangChain LangGraph docs (`application-structure`, `thinking-in-langgraph`,
> `graph-api`, `functional-api`, `fault-tolerance`, `checkpointers`,
> `event-streaming`, `interrupts`, `multi-agent`) and the `langchain-ai/langgraph`
> repository layout (`libs/cli/examples`, `libs/sdk-py/integration/graph`).

This document answers three questions, in order:

1. **Which software architecture?** (hexagonal vs layered vs clean — what actually
   fits LangGraph, and what the ecosystem actually does)
2. **What are the SOTA best practices?** (official app structure, node/state
   design, error taxonomy, control-flow primitives, durability)
3. **What do we learn from professional codebases?** (official repo layout and the
   honest signal about community patterns)

---

## 1. Which software architecture?

### The honest answer: a "clean/hexagonal-inspired layered" hybrid, with LangGraph as the application core

There is **no industry convention called "hexagonal LangGraph"** — a live GitHub
search for `langgraph hexagonal ports adapters architecture` returns essentially
nothing. The ecosystem has converged on a different, simpler shape. That is the
first professional finding: **do not force hexagonal onto LangGraph.**

Instead, the community (and the official repo) uses a **layered structure with
dependency-inversion discipline**, which is *hexagonal in spirit* but simpler in
practice:

```
┌────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL / ADAPTERS  (the outside world — knows nothing about graphs) │
│  • HTTP/CLI entrypoints          • LLM provider SDKs (litellm)         │
│  • Scholarly APIs (OpenAlex…)    • scrapers (crawl4ai…)               │
│  • checkpointer stores (InMemory/Sqlite/Postgres)                     │
└───────────────▲────────────────────────────────────────▲──────────────┘
                │ implements                             │ implements
┌───────────────┴────────────────────────────────────────┴──────────────┐
│  PORTS / INTERFACES  (the contracts the core depends on)              │
│  • ProviderPort · SearchPort · ScrapePort · CheckpointFactory         │
└───────────────▲────────────────────────────────────────▲──────────────┘
                │ depends on (inverted)                   │ depends on
┌───────────────┴────────────────────────────────────────┴──────────────┐
│  APPLICATION CORE  (LangGraph — the orchestrator)                     │
│  • state.py  → typed State + reducers                                 │
│  • nodes/    → node classes (pure, injected ports)                    │
│  • graphs.py → WorkflowRegistry: one graph per workflow               │
│  • runtime.py→ GraphRuntime.run(workflow, thread_id, query)           │
└────────────────────────────────────────────────────────────────────────┘
```

**Why this is the right call, not pure hexagonal and not pure layered:**

| Pattern | Fit for LangGraph | Why |
|---------|-------------------|-----|
| Pure Hexagonal (Ports & Adapters, no other structure) | ❌ Over-structured | LangGraph *is* the application core; it already provides the message-passing backbone, checkpointing, and control flow. Wrapping it behind yet more ports adds a layer with no return. The ecosystem doesn't do it. |
| Pure Layered (controller → service → repo) | ❌ Mismatch | Agent workflows are not CRUD. Routing, retries, interrupts, and fan-out live *inside* the graph — a service/repository split fights the runtime instead of using it. |
| **Clean/hexagonal-inspired layered** (ports at the boundary, LangGraph as the core, adapters outside) | ✅ Sweet spot | You keep the **dependency rule** (core knows nothing about adapters; adapters implement ports) and the **composability** of hexagonal — which is exactly what your existing `core/ports.py` + `container.py` already give you — without duplicating what LangGraph does. |

> **Key takeaway:** keep the **hexagonal boundary discipline you already have**
> (`core/ports.py`, adapters, composition root in `container.py`) — it is correct
> and worth keeping. But the **orchestration core is LangGraph**, not a hand-rolled
> event shell and not a second graph layer. Ports feed *node dependencies*; the
> graph is where the workflow lives.

### Where the current repo deviates (the "hardcoded" smell you flagged)

The current `orchestration/` has **two competing cores**:

- `pipeline.py` — a legacy LangGraph graph where all node logic is **methods on one
  `Pipeline` class** and topology is built imperatively in `_build()`. Hardcoded:
  nodes aren't independent, `_fan()` sends to a node that doesn't exist.
- `event_pipeline.py` + `stations.py` — a **hand-rolled event choreography**
  (ADR 0007) duplicating the same six steps.

That is exactly the anti-pattern the official docs warn against: a graph where you
can't see "what this node does" and "where it can go" at a glance. The fix (below)
is the official shape: **state in one place, one node class per file, graphs
declared, runtime minimal.**

---

## 2. SOTA best practices (from the primary docs)

### 2.1 The official application structure

The official LangGraph app-structure doc and the `langgraph` repo's own examples
(`libs/cli/examples/graphs`, `libs/sdk-py/integration/graph/`) converge on one
shape:

```
my_agent/
├── __init__.py
├── state.py    # state definition of your graph
├── nodes.py    # node functions for your graph
├── tools.py    # tools for your graph
└── graph.py    # code for constructing your graph
```

with a `langgraph.json` (deps + which graph functions are deployable) at the root.
The sdk-py integration dir names them `factory_graph.py`, `streaming_graph.py`,
`tools_agent.py`, `deep_agent.py` — **one graph per file**.

### 2.2 The "thinking in LangGraph" method (5 steps)

The official guide prescribes a repeatable method:

1. **Map the workflow to discrete steps** — each step becomes a *node*; sketch how
   they connect. Notice: some nodes *always* go to the same next step (static
   edge), others *decide* where to go (conditional/`Command`).
2. **Classify each step's type** and its needs:
   - **LLM steps** (understand/analyze/generate) — prompt + dynamic context from state.
   - **Data steps** (retrieve external info) — retry policy + optional caching.
   - **Action steps** (perform external effects) — retry, do-not-cache.
   - **User-input steps** — `interrupt()` for human-in-the-loop.
3. **Design the state** — a shared, typed "notebook". Two hard rules:
   - **Store raw data, not formatted text.** Format prompts *inside* nodes on
     demand. (Prompt templates then never break the state schema.)
   - Persist what can't be derived; compute the rest.
4. **Build nodes as pure functions/classes** — `(state) -> partial update`.
5. **Wire minimal essential edges** — nodes carry their own routing via `Command`.

### 2.3 Node design (the SOLID part you asked about)

Official guidance: **nodes are functions** — "they take state, do work, and return
updates." For a *professional, traceable* codebase we promote each to a small
**class** with an injected dependency, which keeps the official contract while
adding testability and composition:

```python
class NodeBase(Protocol):
    async def run(self, state: WorkflowState) -> dict: ...

class IngestNode(NodeBase):
    """One job: resolve sources. Deps injected, stateless per run."""
    def __init__(self, search: SearchPort, limit: int = 10) -> None: ...
    async def run(self, state: WorkflowState) -> dict:
        return {"sources": await self.search.search(state["query"], limit=self.limit)}
```

Best practices that make nodes SOLID:

- **One file per node** (`nodes/ingest.py`, `relevance.py`, …). The official docs
  justify fine granularity for a concrete reason: *checkpoints happen at node
  boundaries*. Smaller nodes → more frequent checkpoints → less re-execution on
  resume, isolated retry policies per external service, and inspectable
  intermediate state.
- **Nodes are idempotent / replay-safe.** The docs are explicit: on resume or
  retry, the whole node re-runs from the start. Side effects must be idempotent
  (upserts, read-before-write), and `interrupt()` must be the first thing in a
  node (anything before it re-runs).
- **Dependencies via constructor injection** → unit tests use fakes; no live
  LLM/network needed. This is the hexagonal boundary in practice.

### 2.4 Error handling taxonomy (native — replaces hand-rolled fallback)

The docs define exactly who fixes what, and LangGraph has a primitive for each:

| Error type | Who fixes it | Mechanism |
|------------|--------------|-----------|
| Transient (network, 429) | System | `RetryPolicy(max_attempts=…)` + `timeout=` |
| LLM-recoverable (bad tool result) | LLM | store error in state, `Command(goto=agent)` loop |
| User-fixable (missing info) | Human | `interrupt()` — pause, checkpoint, resume |
| Recoverable after retries | Developer | `error_handler(state, NodeError) -> Command(goto=…)` (Saga/compensation) |
| Unexpected | Developer | bubble up |

Plus `StateGraph.set_node_defaults(retry_policy=…, timeout=…, error_handler=…)`
to set graph-wide defaults once instead of per-node. **This subsumes the token
bucket/fallback behavior at the node level** — our `FallbackRouter` stays as the
*adapter* that makes one provider call, and LangGraph's `RetryPolicy`/`timeout`
handle *attempts* at the graph layer.

### 2.5 Control-flow primitives (the "internally event-driven" part)

Verified from the Graph API doc — LangGraph is a **Pregel message-passing state
machine**:

- **Supersteps**: a node writes channel deltas → runtime resolves → schedules next
  nodes. Multiple outgoing edges run destinations **in parallel** in the next
  superstep.
- **`Send(name, state)`** — dynamic map-reduce fan-out (per-source scrape). This
  replaces the dead `_fan()`.
- **`Command(update=…, goto=…)`** — combine state update + routing in one return
  (the judge→synthesize optimizer loop).
- **`interrupt()`** — durable HITL pause (saves checkpoint, holds no connection,
  resumes via `Command(resume=…)`). This is the review gate.
- **`astream_events` / `stream_events`** — the internal event bus (channels:
  `values`, `updates`, `messages`, `tools`, `custom`, `checkpoints`, `tasks`).
  Workflow events stay **inside** the graph — nothing is republished externally.

> Rule (from docs): for each node, pick **one** routing mechanism — static edges
> *or* conditional edges/`Command`, not both — or both paths can run.

### 2.6 Durability & the checkpointer (why we don't need a broker)

- Compile with a checkpointer; every run passes `config={"configurable":
  {"thread_id": …}}`.
- `InMemorySaver` (default, `langgraph-checkpoint`) for dev/tests;
  `SqliteSaver`/`PostgresSaver` via the factory for real durability.
- Checkpoints at **superstep boundaries**; pending-writes recovery means
  successful sibling nodes are not re-run on resume; time-travel/replay come free.
- **This is the crash-recovery mechanism.** With a checkpointer, an in-flight
  request survives a crash by *resuming from the last checkpoint* — which is why
  Redis Streams/XACK is not justified here (durability ≠ throughput, and the
  checkpointer already provides durability). Per your decision: no external EDA.

---

## 3. What we learn from professional codebases

### Official `langchain-ai/langgraph` (41k★)

- **Layout**: `libs/langgraph/langgraph/{graph,pregel,channels,stream,func,…}` —
  framework internals, irrelevant for app code. The *app* guidance lives in
  `libs/cli/examples` and `libs/sdk-py/integration/graph/`:
  - `graph/state.py`, `graph/nodes.py`, `graph/tools.py`, `graph/graph.py`
  - `factory_graph.py`, `streaming_graph.py`, `tools_agent.py`, `deep_agent.py` —
    one graph per file.
  - Every package ships `py.typed`; tests are split `unit_tests/` vs
    `integration_tests/`; heavy use of `conftest.py` fixtures.
- **Tests**: `tests/test_pregel.py`, `test_retry.py`, `test_interruption.py`,
  `test_time_travel.py` — behavior is tested at the graph level, not just the
  node level. This validates our plan: integration tests run compiled graphs with
  fakes + `InMemorySaver`.

### Honest community signal

Searching for "production langgraph template" yields mostly tiny repos. The
high-star real-world weight is in the official repos and the `create_agent` /
Deep Agents surface. Lesson: **the professional move is to use the official
primitives (`StateGraph`, `interrupt`, `Command`, `RetryPolicy`, checkpointer,
MCP adapters) rather than to bolt a bespoke abstraction layer on top.** The
abstraction we add (node classes, one per file) is thin, official-shaped, and
pays for itself in traceability and testability.

---

## 4. Applied: our orchestrator design (summary)

```
orchestration/
├── state.py        WorkflowState (TypedDict + Annotated[list, add] reducer)
│                   to_graph_state() snapshot helper
├── nodes/          one class per file, injected deps, async run(state) -> dict
│   ├── ingest.py       IngestNode(search)
│   ├── relevance.py    RelevanceNode(provider)
│   ├── bodies.py       BodiesNode(scrape)            # Send fan-out per source
│   ├── synthesize.py   SynthesizeNode(provider)      # judge loop target
│   ├── judge.py        JudgeNode(provider)           # Command(goto=…) routing
│   ├── review.py       ReviewNode(interrupt)         # HITL gate
│   └── render.py       RenderNode(store)             # output artifact
├── graphs.py        WorkflowRegistry: research/qa/summary graphs
│                   StateGraph + set_node_defaults + retry/timeout/error_handler
├── checkpoint.py    build_checkpointer("memory"|"sqlite"|"postgres")  # ONLY persistence abstraction
└── runtime.py       GraphRuntime.run(workflow, thread_id, query) -> GraphState
                    (single external surface; drives astream_events internally)
```

**External surface (per your decision): one request in → one `GraphState` out.**
No Redis, no brokers, no event republishing. All workflow events are LangGraph's
internal Pregel events, checkpointed at superstep boundaries.

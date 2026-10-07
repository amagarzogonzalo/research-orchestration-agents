---
@id: "plans/0005"
type: ADR
status: ACCEPTED
date: 2026-09-03
related:
  - 0001
  - 0002
  - 0004
---

# 0005 — LangGraph Orchestration: State Machine Over Naive Autonomy

## Context

We must encode a *research curation* workflow whose step count is mostly bounded but whose *outcomes* are probabilistic. Per Anthropic's production-agent taxonomy, we favor **deterministic workflows** (prompt chaining, routing, parallelization, orchestrator-worker, evaluator-optimizer) and reserve fully autonomous loops only where the step count is unguessable. The blueprint (`docs/instruction.md`) explicitly demands these patterns plus Postgres/Redis checkpointing.

## Decision

Implement a **LangGraph state machine** (`langgraph==1.2.x`) with a **Postgres checkpointer** (`langgraph-checkpoint-postgres==3.1.x` on Neon) as the durable source of truth. Workers that talk to providers live in the event-driven shell (`0001`) — the graph is the *orchestrator*, not the executor of vendor calls.

## Graph topology (nodes + edges)

```
[INGEST] --route--> [RELEVANCE-ROUTE] --> bool --> [FILTERED]
                        | reject: [FILTERED-EMPTY] -> TERMINAL

[FILTERED] ==parallel==> [FETCH-BODIES]      (crawl4ai/scrape port, async fan-out)
                        [SYNTHESIZE]         (Gemini "million" affinity, eval-opt loop)
                        [EXTRACT-ENTITIES]   (Instructor -> Pydantic)

[FETCH-BODIES]/[SYNTHESIZE]/[EXTRACT] == join ==>
    [JUDGE] (LLM-as-a-judge: groundedness + citation integrity vs golden set)
       | pass -> [KNOWLEDGE-RENDER] -> [KNOWLEDGE-BASE-LOAD]
       | fail -> [SYNTHESIZE] again (evaluator-optimizer, max_iterations=N)
```

- `RELEVANCE-ROUTE`: cheap boolean filter via **Groq** (`affinity=micro`) — 50-token `tldr_llm` gate.
- `SYNTHESIZE`: high-context Gemini / Mistral; the abstractive core.
- `JUDGE`: groundedness + citation-integrity scoring; on fail, iterate synthesize with the critique injected (Reflexion-style).
- Parallel nodes use `Send()` fan-out so independent source fetches run concurrently, bounded by the token bucket.

## Checkpointing & Human-in-the-Loop

```python
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import StateGraph, START, END

checkpointer = PostgresSaver(conn_string=NEON_DSN)   # durable across restarts
graph = build_pipeline().compile(checkpointer=checkpointer)

# resume-able config; a reviewer interrupt lives at [KNOWLEDGE-RENDER]
config = {"configurable": {"thread_id": state.thread_id}}
```

Interrupts surface as `graph.get_state(config).next`; a reviewer approves via `graph.update_state(...)` and the run resumes without replaying provider calls (critical for token budget).

## Broker boundary contract

The graph **never** calls providers directly. Each node emits a stream event to `inbox.<provider>` (`0001`) and awaits a `done` reply (**async, deadline-bounded**). This composes the deterministic graph with the resilient shell:

```python
async def fetch_bodies(state: GraphState):
    return {"bodies": await shell.fetch_all(state.filtered, deadline_s=90)}
```

## Edge cases hardened
- **Bounded iterations**: evaluate loop stops at `max_iterations` (default 2) and returns best-effort + `judge_score`, never spins.
- **Partial fan-out**: an individual source fetch failure is a `None` body filtered before Synthesis, not a graph abort.
- **Checkpointer durability**: Neon holds the thread state, so a worker restart resumes the exact step.
- **Concurrency bounded** by token buckets — a parallel fan-out cannot oversubscribe RPM.

## References
- `docs/instruction.md` §Agent Orchestration; Anthropic "Building Effective Agents" taxonomy; Reflexion (critique-driven iterate) as the theoretical basis for `JUDGE`.
- Reference impl: `langchain-ai/langgraph` supervisor/checkpoint docs; Letta (`letta==0.16.x`) for memory paging integration on long-horizon runs.

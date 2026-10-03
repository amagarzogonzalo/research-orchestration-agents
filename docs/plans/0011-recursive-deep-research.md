# 0011 — Recursive deep-research workflow (`deep`)

## Context

Single-pass `research` cannot cover multi-aspect questions; the codebase had no
decomposition, no handoffs, and thin guardrails. PaperPilot's answer is
homogeneous recursion (same agent graph, depth + budget vary) with shared
budgets and rubric-driven stopping — adapted here to zero-cost free-LLM and
Supabase persistence constraints.

## Decision

- **Round-based replan, not tree recursion.** One planner (`DecomposeNode`)
  fans out via `Send` to homogeneous `BranchNode` workers (each runs the
  existing ingest → relevance → fetch → synthesize leaf pipeline); `AggregateNode`
  merges findings and scores the deterministic rubric; `deep_route` replans on
  gaps (≤ `MAX_DEEP_ROUNDS=2`) or advances to `CriticNode → judge → review`.
  True self-`Send` nesting is a follow-up (unverified on langgraph 1.2.x).
- **Unified entry.** `IntentNode` routes chat/memory/quick/deep deterministically;
  only ambiguous input spends one MICRO call; any failure fails open to `deep`.
  Non-deep intents short-circuit to `synthesize → render` (never enter the loop).
- **Critic advises, judge decides.** `CriticNode` emits structured rubric feedback
  (`CriticReport`: coverage/diversity/grounding/contradictions/unanswered) into
  `gaps`; `JudgeNode` keeps its pass/fail gate; deterministic fallback always exists.
- **Shared budgets, enforced twice.** `DEPTH=2, FANOUT=5, ROUNDS=2, CALLS=12,
  RUBRIC=0.7` enforced in nodes (depth guard skips the LLM call) and routes
  (`should_stop`). Branches write add-reducer keys/deltas only; per-branch
  retrieval capped at 5 sources (federated-quota discipline incl. Crossref).
- **MCP both directions.** Branches tagged `mcp` use an MCP-backed search port
  (federated fallback); `research_server.py` exposes `research_quick` /
  `research_deep` as in-process FastMCP tools for outside agents.
- **Supabase roles.** Checkpointer persists every super-step (crash-resume across
  rounds, durable review interrupt); store holds cross-thread memory (final plan
  summary post-run). Branch intermediates ride the checkpointer, not the store.
- **Studio.** `studio.py` exposes memory-backed `graph_deep`/`graph_research`;
  `langgraph.json` points at them with `dependencies: ["."]`.

## Consequences

- `deep` runs only on request; `research/qa/summary` byte-identical.
- Worst case per run ≈ 5 branches × (1 search + 3 fetches) + ≤13 LLM calls.
- Property tests pin caps/termination; `evals/cases/deep.yaml` pins behavior.
- Non-goals: tree recursion, semantic branch retrieval, Wiki/Evidence layers.

---
@id: "plans/0011"
type: ADR
status: ACCEPTED
date: 2026-10-07
related:
  - 0008   # LangGraph orchestrator: retry/timeout/error-handler defaults
  - 0009   # portable research library
---

# 0011 — Derived run limits: recursion bounds computed from budget constants

## Context

`GraphRuntime.run()` invoked compiled graphs without an explicit
`recursion_limit`, so every run inherited LangGraph's default (25). The deep
workflow's worst case (intent + 3 replan rounds x 6 supersteps + review/render
tail) is ~22 supersteps — one budget change away from a spurious
`GraphRecursionError` in production — while a runaway conditional-edge loop
would still spin unbounded above the soft `guardrails` caps. Two different
failure modes, one missing knob.

## Decision

1. `orchestration/limits.py` derives `recursion_limit` per workflow from the
   SAME constants the nodes enforce (`MAX_DEEP_ROUNDS`,
   `MAX_SYNTHESIS_ITERATIONS`); `DEFAULT_LIMITS` is computed at import, never
   hand-copied.
2. Pregel accounting: `Send` fan-out shares one superstep; node retries
   re-attempt within their superstep — limits do not scale with branch or
   retry count.
3. `GraphRuntime.run()` / `resume()` inject the derived limit into the
   `ainvoke` config; callers may override per call.
4. Safety multiplier x3 with a documented worst-case derivation; a property
   test (hypothesis) asserts the limit dominates any reachable superstep
   count, and a unit test asserts raising a budget constant raises the limit.

## Consequences

- **Positive:** hard loop bound independent of guardrail correctness; budget
  changes visibly propagate to the bound; fully offline-testable.
- **Negative:** a deliberately deeper fan-out requires touching the budget
  constants (deliberate friction per repo rule 6 — raise the budget knowingly,
  never accidentally).

## References

- `docs/langgraph/architecture.md` §2.5 — control-flow primitives, Pregel
  superstep model.
- `orchestration/guardrails.py` — soft bounds (depth/rounds/LLM-call caps).

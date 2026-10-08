---
@id: "plans/0016"
type: ADR
status: ACCEPTED
date: 2026-10-07
related:
  - 0008   # LangGraph orchestrator (pure-node rule this conformants to)
  - 0013   # state v2 body refs
  - 0014   # derived run limits
---

# 0016 — Per-run settings channel: overrides riding state, validated at the facade

## Context

Run knobs (`MAX_DECOMPOSE_DEPTH`, `MAX_SUBQUERIES`, `MAX_SYNTHESIS_ITERATIONS`)
were module constants baked into node constructors — a deeper one-off run
required a code change and a redeploy. The standard LangGraph answer
(`config["configurable"]` injection) violates the repo's own hard rule:
*nodes stay pure `(State) -> State`*.

## Decision

1. `schemas/settings.py: RunSettings` — Pydantic, `extra="forbid"`,
   optional-only fields (`max_depth`, `max_subqueries`, `max_iterations`).
   A closed surface: unknown keys or model names are rejected at the
   boundary (models come from discovery, never from run settings).
2. `engine.research(..., settings=RunSettings(...))` validates once and
   writes `settings.model_dump()` into the initial state; `GraphRuntime.run`
   passes it through. Nodes read via `state.read_setting(state, key,
   CONSTANT)` — defaults are the same budget constants (single source of
   truth, DIP).
3. Defaults ≡ current constants → prompts byte-identical → eval cassettes
   replay untouched (asserted by the golden master + eval suite).
4. Deliberate friction: overrides are explicit per run; nothing changes
   globally. Rule 6 discipline — raising a cap is a visible caller choice.

## Consequences

- **Positive:** per-run knobs without redeploys; tests exercise budget
  variants without monkeypatching; the settings channel documents itself in
  `metadata.json` (provenance).
- **Negative:** one more state channel to document; overrides are
  single-writer (engine facade) — concurrent writers would need a reducer.
- **Zero-cost:** no new dependency; validation is one Pydantic pass per run.

## References

- `tests/unit/test_settings.py`, `tests/integration/test_settings_channel.py`.
- `orchestration/state.py: read_setting`.

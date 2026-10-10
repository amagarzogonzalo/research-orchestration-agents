# 0030 — Dynamic closure: universal wiki wiring + live use cases + active eval case + tooling

**Status:** Delivered · **Date:** 2026-10-09 · **Branch:** `feature/llm-wiki-loop0`

## 1. Universal wiki layer (SOLID)

`graphs.py` factored the wiki wiring into three single-purpose helpers and
applied them to **every** workflow:

- `_wire_wiki_prefix` — `START → wiki_query` (hit → render, miss → the
  workflow's normal entry; `wiki_route` now routes deep-miss to `intent`).
- `_wire_clean` — dedup between ingest and its consumer (research/qa/summary).
- `_wire_wiki_suffix` — `render → wiki_ingest → END`.

qa + summary now compile into the wiki; deep gains wiki lookup + compile
(no parent-ingest dedup — its ingest lives inside branch workers). Bench
re-baselined: deep supersteps 13→15, checkpoints +10% — the documented cost
of universal wiring (RAM 574→651kB, well inside budget). Deep eval replay
stayed bit-identical (wiki wiped per case; artifacts unchanged).

## 2. TDD coverage added

- `tests/e2e/test_wiki_hit_path.py` extended: qa and summary both compile
  into the wiki; research hit/miss unchanged.
- Dynamic fixes surfaced by running: LangGraph validates conditional targets
  against registered nodes → the conditional map now declares only the entry
  that exists in each graph; deep has no `ingest` node → `_wire_wiki_prefix`
  takes an explicit `entry`.

## 3. Live use cases (real Supabase + real free-tier LLM)

- **Deep run into the cloud wiki**: `deep` workflow ran live (search_limit=2),
  status done, filed to the real `wiki` bucket; `search_wiki` found both the
  deep page and the earlier research page.
- **lint_deep with real provider**: populated a vault with two live research
  runs, ran `WikiDeepLinter` with the engine's default provider — reported a
  thin page (from a degraded run) + 2 knowledge gaps. Correct signal, not a
  bug.
- **Graph on a real vault**: `WikiGraph.from_vault` → 2 nodes, 1 edge,
  1 community.

## 4. Eval closure (human-directed snapshot)

`internal_wiki_demo` was recorded live (17671 tok / 18 calls), **replayed
bit-identical** (same tokens/calls/artifact), then the snapshot was accepted
(on the user's direction — the one repo rule the agent otherwise never
overrides). The case is now `draft: false` and gates `make evals` at 6/6.

## 5. Tooling + docs

- `bin/wiki_graph.py` + `make wiki-graph` (graph JSON dump).
- README gains a compact "Wiki layer" section.
- Cassettes + snapshot for `internal_wiki_demo` committed with this change.

## 6. Gates

ruff/format/mypy(strict)/tach/pydoclint/semgrep green; `pytest tests/`
green; evals **6/6 green**; bench green; context 2406/6000.

## 7. Still human-gated (genuinely cannot be done by the agent)

- Real Supabase provisioning uses your `.env` keys — the bucket already
  exists and is verified live; `wiki_store_kind="supabase"` activates it.
- Nothing else remains open from the plan.

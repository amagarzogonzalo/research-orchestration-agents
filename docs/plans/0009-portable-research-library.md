---
@id: "plans/0009"
type: ADR
status: ACCEPTED
date: 2026-09-06
supersedes:
  - 0007   # revoked for good: no external event shell at all
related:
  - 0008   # LangGraph orchestrator (kept, checkpointer swapped to files)
  - 0001   # hexagonal boundary (kept)
---

# 0009 — Portable Research Library: Files-Only Persistence, Browser Automation, Free-LLM Discovery

## Context

The repo's true objective is a **lightweight, portable research library** that
another project imports to do research, and that is trivial to move between
projects. That goal conflicts with server-bound persistence (Redis Streams,
Postgres/Neon, SQLite checkpoints, Qdrant) and with heavy optional infra
(Letta memory, vector stores). Separately, two real capabilities were missing:

1. **Full browser automation** (Selenium/Playwright) — scrape web content, submit
   forms, or test UIs, not just read-only URL→Markdown.
2. **Free-LLM discovery** — a professional service that discovers currently-free
   models, health-checks them cheaply, and selects a working one (adapted from
   the sibling `digest_agent` codebase).

## Decision

1. **Cut all server/adapter infra.** Delete `infrastructure/{store,vector,memory}`
   (neon/qdrant/letta), `redis`, `psycopg`, `langgraph-checkpoint-{sqlite,postgres}`,
   and the corresponding extras. No SQLite, no Postgres, no Redis, no vector DB.

2. **Persistence is files-only.** New `store/` package:
   - `ResearchStore` — every run writes `metadata.json`, `events.jsonl`,
     `sources.json`, `article.md` (+ YAML frontmatter) under `research_store/<thread_id>/`.
   - `FileCheckpointer` — a `BaseCheckpointSaver` backed by JSON files
     (`research_store/checkpoints/<thread_id>/*.json`) so LangGraph's durability
     and `interrupt()`/HITL still work, portably. Copy the folder, move the state.

3. **Browser automation is a first-class port.** `BrowserPort` +
   `BrowserActionPort` implemented by `PlaywrightBrowser` (SOTA, async) and
   `SeleniumBrowser` (fallback) behind one `act(action, **kwargs)` surface,
   exposed to agents via an MCP `browser_act` tool. Distinction: deterministic
   render stays on `ScrapePort`; actions (click/fill/submit/assert/screenshot)
   go through the browser port.

4. **Free-LLM discovery service** (adapted from digest_agent). A port
   (`LLMDiscoveryPort`) with `list_free_models` / `test` / `complete`; adapters:
   `OpenRouterCatalog` (dynamic `/models` scan filtered to $0, ranked by context),
   `GroqDiscovery`/`MistralDiscovery` (fixed OpenAI-compatible lists),
   `GeminiDiscovery` (REST). `FallbackDiscovery` chains them; pure
   `ModelSelectionPolicy` runs the evaluator-optimizer loop; `ModelDiscoverer`
   is the high-level entry ("find a working free model now").

   **Lean / repeat-safe design (no saturation):** the free-model catalog is
   cached (30 min), a *known-good* model is remembered with a 5-min TTL so
   repeated `discover()`/`complete()` calls reuse it without re-testing, and
   providers that just rate-limited/timed out are skipped for a 60s cooldown.
   Selection is pure logic over cheap health-checks — no LLM is used to "judge"
   a candidate, so each discovery costs the minimum possible tokens. Verified
   live: `select()` picked `nvidia/nemotron-3.5-lightning:free` and repeated
   `complete()` calls issued zero extra health-checks.

5. **One public facade.** `ResearchEngine` is the single import surface:
   `research/search_run/scrape_run/browser_act/discover/resume`, all writing to
   a local `research_store/`.

6. **Documentation enforcement.** Google-style docstrings required on every
   function/class, enforced in pre-commit + CI via `pydoclint --style google`
   (type hints kept in signatures, `--arg-type-hints-in-docstring 0`).

## Consequences

- **Positive:** fully portable (a folder); zero server ops; lightweight install
  (no redis/psycopg/qdrant/letta); browser automation for agents; free-LLM
  discovery without hardcoded vendor pins; docstrings enforced by the gate.
- **Negative:** no cross-process event bus (LangGraph events stay internal);
  durable checkpoints are per-run folders instead of a shared DB; adapter glue
  wrapping untyped vendor SDKs (playwright/selenium/httpx) is exempt from the
  strict mypy gate (documented in pyproject).
- **Zero-cost:** unchanged — free tiers only; observability no-op unless exported.

## References

- `docs/langgraph/architecture.md` — LangGraph SOTA analysis (kept).
- Adapted free-LLM discovery pattern from `digest_agent`
  (`ports/llm_provider.py`, `adapters/driven/*`, `core/policy.py`).
- SQLite FTS5 / DuckDB research: not adopted (files-only was chosen); would be a
  future opt-in `[store]` extra if corpus-scale retrieval is ever required.

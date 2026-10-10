# LLM_WIKI.md — Delivery plan: LLM wiki on the research orchestrator

**Status:** ALL LOOPS SHIPPED + next-increment batch · **Date:** 2026-10-09 **Base:** audits 0017–0027
**Contract:** Loop 0 ships ALL core features working; Loops 1–5 are dynamic
improvement loops driven by TDD findings and real use-case checks — each ends
`make check` green.

## The core mapping (read this first)

**The research the orchestrator finds is the content the wiki ingests.**

- **Raw layer = the research output.** `research_store/<thread>/article.md`,
  `sources.json`, `metadata.json`, `events.jsonl`, plus scraped full-text
  bodies under `<store_root>/bodies/<sha256>.md`. Immutable, never edited.
- **Ingested = that same output.** Every finished run is filed into the wiki
  as a `sources/<slug>.md` page carrying `thread`/`sha256`/`query_sha256`
  lineage back to the raw run.
- **Compiled = LLM-added layers (opt-in).** Concept/entity pages, thesis
  verdicts, and filed answers distilled from the ingested research.
- So: *orchestrator does the research; wiki is where it is ingested, deduped,
  cross-linked, and kept current.* See `docs/MR-LLM-WIKI.md` for the human
  overview and `knowledge_base/WIKI_SCHEMA.md` for the contract.

## 0. Everything shipped (this branch, one commit per loop)

| Loop | Delivered | Evidence gate |
|---|---|---|
| **0** | Wiki-first `research()`: exact-query hit → render with zero live calls; miss byte-identical. `CleanNode` (exact-id, close-never-delete). `WikiIngestNode` (NEW/UPDATE/NO_MATERIAL + SHA cache + index/log/overview). `FileVaultLinter`. `SupabaseWikiStore` no-op-until-configured. | 5 existing evals replay bit-identical; `internal_wiki_demo` draft; bench re-baselined (RAM improved 696→473kB). |
| **1** | Two-step LLM ingest (`settings.wiki_extract`), concept/entity **drafts** + review (approve with hand-edit hash guard / reject with feedback injected into next compile), fail-closed placeholder gate, `confidence` labels, `purpose.md`. | New `test_wiki_quality.py` (8 tests): opt-in, fail-closed, placeholder rejection, thesis, cap, review API. |
| **2** | **Measured** (not guessed): `bin/clean_report.py` → 330 records / 0 intra-batch dupes / **21 cross-thread DOIs**. Dedup therefore lives at the wiki layer: cross-thread content-sha dedup → link, never duplicate. LSH signature path (>64) + false-merge guards. | `make wiki-lint`-style unit gates; report printed in commit message. |
| **3** | Cloud cache coherence (bucket `.cache.json` merged across processes), `SupabaseVaultLinter` (shared `lint_core`), live-marked integration test (skipped without keys). | `test_supabase_vault_lint.py` + live test + contract round-trip. |
| **4** | Derivable `.wiki.db` FTS5 sidecar: build/search(BM25)/rebuild/delete-rebuild-idempotent, incremental insert on ingest, **scan-scoring fallback** when FTS5 unavailable. | `test_sidecar.py` (FTS5-skip guarded). |
| **5** | Thesis verdict pages (`settings.thesis`), collector caps (`max_wiki_pages`), round budgets (existing ADR 0014), and the **wiki health gate** wired into the toolchain: `make wiki-lint`. | End-to-end probe: ingest → search → lint ok → gate green. |
| **+1 (next inc.)** | **Answers filed back** (`settings.wiki_save_answers` → `queries/`), **contradiction annotation** (`flag_contradiction` → DISPUTED status, close-never-delete), **LLM cross-page lint** (`WikiDeepLinter` — contradictions/stale/gaps, degrade-safe, opt-in), **draft review CLI** (`bin/wiki_review.py` list/accept/reject). | `test_wiki_quality`, `test_deep_lint` + new `test_save_answers`; live supabase probe made run-idempotent. |
| **+2 (finish)** | **Offline e2e wiki-hit proof** (`tests/e2e/test_wiki_hit_path.py` — real graph + failing live adapters, zero calls on hit), **auto-block after 5 rejections** (`_MAX_REJECTIONS`, unblock via CLI), **WikiGraph** (nodes/edges/backlinks + label-propagation communities, 4-signal source-overlap) + `bin/wiki_graph.py` style export via `.to_json()`, **hybrid RRF search** (FTS5 + scan fusion, k=60, deterministic), **Supabase provisioning script** (`bin/wiki_supabase_provision.py` — creates `wiki` bucket, documents activation). | `test_graph`, `test_sidecar` hybrid tests, e2e hit-path, no-key fail-loud script check. |

## 1. Knobs (all optional; zero-cost defaults)

- `engine.research(q, settings=RunSettings(wiki_extract=True, wiki_save_answers=True, thesis="...", max_wiki_pages=N))`
- Off by default ⇒ every existing behavior is untouched; the wiki compiles via
  deterministic filing until you opt in to the LLM-compiled layer.

## 2. Tooling (this increment)

- `make wiki-lint` — deterministic health gate (structural).
- `make wiki-lint-deep` — structural + opt-in LLM contradictions/stale/gaps
  (degrades to structural when no provider is configured).
- `make wiki-review` — list concept/entity drafts; accept/reject with feedback.

## 3. Rollback

Per loop commit on `feature/llm-wiki-loop0`; revert that commit; engine reset
`file|memory|local`; `git status --porcelain research_store knowledge_base src evals/snapshots` clean.

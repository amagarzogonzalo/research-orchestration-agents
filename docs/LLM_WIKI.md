# LLM_WIKI.md — Delivery plan: LLM wiki on the research orchestrator

**Status:** ALL LOOPS SHIPPED · **Date:** 2026-10-09 · **Base:** audits 0017–0026
**Contract:** Loop 0 ships ALL core features working; Loops 1–5 are dynamic
improvement loops driven by TDD findings and real use-case checks — each ends
`make check` green.

## 0. Everything shipped (this branch, one commit per loop)

| Loop | Delivered | Evidence gate |
|---|---|---|
| **0** | Wiki-first `research()`: exact-query hit → render with zero live calls; miss byte-identical. `CleanNode` (exact-id, close-never-delete). `WikiIngestNode` (NEW/UPDATE/NO_MATERIAL + SHA cache + index/log/overview). `FileVaultLinter`. `SupabaseWikiStore` no-op-until-configured. | 5 existing evals replay bit-identical; `internal_wiki_demo` draft; bench re-baselined (RAM improved 696→473kB). |
| **1** | Two-step LLM ingest (`settings.wiki_extract`), concept/entity **drafts** + review (approve with hand-edit hash guard / reject with feedback injected into next compile), fail-closed placeholder gate, `confidence` labels, `purpose.md`. | New `test_wiki_quality.py` (8 tests): opt-in, fail-closed, placeholder rejection, thesis, cap, review API. |
| **2** | **Measured** (not guessed): `bin/clean_report.py` → 330 records / 0 intra-batch dupes / **21 cross-thread DOIs**. Dedup therefore lives at the wiki layer: cross-thread content-sha dedup → link, never duplicate. LSH signature path (>64) + false-merge guards. | `make wiki-lint`-style unit gates; report printed in commit message. |
| **3** | Cloud cache coherence (bucket `.cache.json` merged across processes), `SupabaseVaultLinter` (shared `lint_core`), live-marked integration test (skipped without keys). | `test_supabase_vault_lint.py` + live test + contract round-trip. |
| **4** | Derivable `.wiki.db` FTS5 sidecar: build/search(BM25)/rebuild/delete-rebuild-idempotent, incremental insert on ingest, **scan-scoring fallback** when FTS5 unavailable. | `test_sidecar.py` (FTS5-skip guarded). |
| **5** | Thesis verdict pages (`settings.thesis`), collector caps (`max_wiki_pages`), round budgets (existing ADR 0014), and the **wiki health gate** wired into the toolchain: `make wiki-lint`. | End-to-end probe: ingest → search → lint ok → gate green. |

## 1. Knobs (all optional; zero-cost defaults)

- `engine.research(q, settings=RunSettings(wiki_extract=True, thesis="...", max_wiki_pages=N))`
- Off by default ⇒ every existing behavior is untouched; the wiki compiles via
  deterministic filing until you opt in to the LLM-compiled layer.

## 2. Rollback

Per loop commit on `feature/llm-wiki-loop0`; revert that commit; engine reset
`file|memory|local`; `git status --porcelain research_store knowledge_base src evals/snapshots` clean.

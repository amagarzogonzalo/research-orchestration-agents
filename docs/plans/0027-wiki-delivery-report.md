# 0027 — Wiki delivery report: the full LLM wiki plan shipped (Loops 0–5)

**Status:** Delivered · **Date:** 2026-10-09 · **Type:** delivery report
**Base:** docs/LLM_WIKI.md · **Branch:** `feature/llm-wiki-loop0`

## 1. What changed (behavior)

`research()` is now **wiki-first**: before any live federated call, the
compiled wiki at `<store_root>/wiki` is consulted. An exact-query hit (page
filed for the same query-sha) routes straight to render — zero live search,
scrape, or LLM calls on a repeat question. A miss runs the full pipeline
byte-identically to the pre-wiki graph. On completion the run is compiled
into the wiki (guaranteed source page), dedup is enforced at the wiki layer
(identical content across threads links instead of duplicating), and a
maintainable index/log/overview are kept consistent.

Opt-in powers (off by default): two-step LLM concept/entity compile with a
human review loop (`archive/reject` + feedback into the next compile),
thesis verdict pages, page-count caps, FTS5 sidecar search at scale, and a
Supabase cloud mirror.

## 2. Files (new)

- `schemas/wiki.py`, `schemas/clean.py`
- `core/ports.py` (+`WikiQueryPort`/`WikiIngestPort`/`CleanPort`/`VaultLintPort`)
- `infrastructure/wiki/` — `file_store.py`, `supabase_store.py`, `lint.py`,
  `lint_core.py`, `sidecar.py`, `common.py`
- `infrastructure/clean/cleaner.py`
- `orchestration/nodes/` — `wiki_query.py`, `wiki_ingest.py`, `clean.py`
- `bin/clean_report.py`, `bin/wiki_lint.py`; `Makefile` (+`make wiki-lint`)
- `docs/LLM_WIKI.md`, `knowledge_base/WIKI_SCHEMA.md`, `knowledge_base/purpose.md`
- tests: `tests/unit/wiki/*`, `tests/unit/test_cleaner.py`, `test_vault_lint.py`,
  `tests/integration/test_supabase_wiki_store.py`
- `evals/cases/internal_wiki_demo.yaml` (draft until live record + human accept)
- audits `docs/plans/0017–0026`, skill `skills/wiki-repo-audit`

## 3. Evals + gates (definition of done)

- `make evals` 6/6 green (5 existing bit-identical + `internal_wiki_demo` draft).
- ruff, ruff-format, mypy strict (125 files), tach, semgrep, agents, pydoclint
  all green; `make context` 2406/6000; `make bench --check` green on re-baseline.
- Commits: Loops 0–5 landed as 7 commits on `feature/llm-wiki-loop0`.

## 4. Eval coverage of the new layer

- Deterministic contract suites ran against every wiki/clean adapter (fake +
  file + supabase) — LSP proof.
- `internal_wiki_demo` covers the wiki-hit path but requires a first live
  record + human `--accept` per evals discipline, so it ships as a draft case
  (never blocks the gate; reports signal).

## 5. What a reviewer might expect but was deliberately NOT done

- No new pip dependency or MCP server (vendored hash logic; stdlib sqlite3).
- No live re-records; no snapshot acceptance by the agent (human-only).
- Semantic embedding dedup tier stays off pending measured recall on a real
  corpus (measurement tool ships in Loop 2; flag lives in `SourceCleaner`).
- Hybrid RRF search deferred — FTS5 BM25 is the scale path; RRF is an opt-in
  extra only if FTS5 recall measurably degrades.
- No UI/graph viewer, no Obsidian plugin, no paid tiers (zero-cost discipline).
- No Supabase provisioning (requires your keys; adapter is ready and tested
  against a fake + a live-marked test).

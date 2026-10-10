# 0028 — Next increment: answers filed back + contradictions + LLM cross-page lint + review CLI

**Status:** Delivered · **Date:** 2026-10-09 · **Parent:** 0027 (all loops shipped)
**Branch:** `feature/llm-wiki-loop0`

## 1. Why this increment

0027 shipped the core wiki; four Karpathy-pattern behaviors were still missing:
answers that "shouldn't disappear" (queries/), contradiction handling (DISPUTED
status), the lint's LLM half (cross-page contradictions/stale/gaps), and a
human review surface for the concept/entity drafts.

## 2. What changed

- `WikiIngestPort.file_answer` + adapters (local under `queries/`, cloud under
  bucket `queries/`) + node honors `settings.wiki_save_answers` → renders file
  the answer page, updates index (## Queries) + log. `PAGE_DIRS` gains `queries`
  so answers are searchable + linted.
- `FileWikiStore.flag_contradiction(slug, claim)` — appends the claim under
  `## Contradictions`, sets `status: disputed`, never deletes prior content
  (close-never-delete). `disputed_slugs()` lists them. `WikiVerdict.DISPUTED`
  is now emitted.
- `WikiDeepLinter(VaultLintPort)` — deterministic scan + optional provider pass
  producing `DeepLintReport` (contradictions/stale/gaps). Degrade-safe: no
  provider / provider failure => structural-only, never a raise, never a paid call.
- `bin/wiki_review.py` (list/accept/reject + feedback) and `make wiki-review`;
  `bin/wiki_lint_deep.py` + `make wiki-lint-deep`.

## 3. Tests

- `test_wiki_quality` + new `test_save_answers` (node → `file_answer`),
  contradiction lifecycle (draft → approve → `flag_contradiction` → disputed +
  content preserved + log entry), `test_deep_lint` (structural-only, findings
  propagate, bad JSON degrades, empty vault skips the model), settings roundtrip.
- Live supabase probe made run-idempotent (unique thread + query) so repeated
  live runs assert `new` deterministically.

## 4. Gates

ruff/ruff-format/mypy(strict)/tach/semgrep/pydoclint green; `pytest tests/`
green (exit 0); evals 6/6; bench green; `make wiki-lint`/`wiki-lint-deep`/`wiki-review` runnable.

## 5. Deliberately not done

Eval snapshot coverage for the hit path (still the draft `internal_wiki_demo`
awaiting a live record + human `--accept`); auto-block after N rejections;
knowledge graph / 4-signal relevance; hybrid RRF search; Supabase provisioning
(your keys); Obsidian/UI.

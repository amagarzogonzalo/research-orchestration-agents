# 0021 — Loop 4 v4 winner: consolidated SOLID + TDD delivery + eval gates

**Status:** Proposed (winner) · **Date:** 2026-10-08 · **Type:** delivery plan
**Parents:** 0018 + 0019 + 0020 · **Rejects:** single-cleaner (superseded by 0019 tiered), loose-files wiki (superseded by 0020 vault+index), embeddings-first search (deferred: FTS5 default per cost/latency evidence)
**Online set:** CiteEval citation precision/recall, C2-Faith causality+coverage, FaithLM intervention fidelity, FACT-E intra-chain faithfulness, FaithEval unanswerable/inconsistent/counterfactual, judge-reliability kappa-deflation study, plus repo `evals/README.md` record/replay/accept + 0018-0020 sets

> For humans: we build in five small safe steps, each with its own test. No big
> rewrite. The robot judge is only advisory, the strict checks (citations, links,
> budgets, replay identical) are what block a bad change. You can undo any step
> by deleting one folder.

## 1. Winner scope (vertical slices, each `make check` green)

- S0 ports+fakes+contracts: `WikiQueryPort`, `WikiIngestPort`, `DedupPort`, `VaultLintPort` in `core/ports.py`; fakes in `tests/conftest.py`; contract suites `test_wiki_store_contract.py`, `test_dedup_contract.py`, `test_vault_lint_contract.py` parametrized over fake+file adapters. Proves LSP.
- S1 internal-first query: `WikiQueryNode` before `IngestNode`; hit cites wiki + narrows live search; miss proceeds + marks gap. Fake-store unit (search spy not called on hit), cassette integration (hit=zero live calls, miss=bit-identical).
- S2 tiered dedup: exact -> SimHash/MinHash-LSH + union-find -> cosine on candidates -> LLM adjudication <1%. Golden pairs + `minhash-dedupe`-style report (scanned/candidates/confirmed/clusters/kept/removed). Thresholds: SimHash Hamming<=3, MinHash Jaccard 0.6 candidate/0.8 merge, cosine 0.8 dup / 0.6-0.8 adjudicate. Close-don't-delete with `superseded_by` + timestamps + valid/belief time.
- S3 vault+index migration: canonical schema (0018+0020), `_index.md`+`log.md` auto-refresh, `.wiki.db` FTS5 sidecar (stdlib sqlite3, deletable/rebuildable), rename rewrites backlinks, history backups. Golden-file migration + delete-rebuild + traverse tests.
- S4 lint+eval: deterministic lint JSON gate (broken links, orphans, frontmatter, thin, index drift) + LLM lint for contradictions/stale/gaps + new case `evals/cases/internal_wiki_demo.yaml` (draft shipped with this plan). No snapshot accept yet; replay must be bit-identical before human `--accept`.

## 2. Eval design (deterministic gate, judge advisory)

Invariants: frontmatter keys (`@id,type,title,provenance,citations,tldr_llm`), min_sections 2, min_citations 3, `must_cite` seeded from fixture, `no_uncited_claims`, `valid_links`, max_chars 30000, plus wiki extras: `index_consistent`, `log_appended`, `lineage_complete` (checked by lint script, reported as invariant failures).
Budget: `max_total_tokens` 30000, `max_model_calls` 32, `max_wall_s` 1200 (mirror `research.yaml` calibration; internal-hit run must come in far under, proving saved quota).
Judge: rubric `rubrics/default.md`, advisory true (kappa-deflation + position-bias evidence says keep advisory until calibrated). DeepEval opt-in only.
Record/replay: wipe `evals/cassettes/internal_wiki_demo/run0/` first, record once with memo, replay bit-identical, review diff like code, never raise budget to fit.

## 3. What reviewer might expect but we do NOT do

No new pip dep (vendored hash logic, stdlib sqlite3+FTS5 only); no new MCP server (tool weight budget in `harness.toml` stays 40); no Supabase cutover in these slices (adapter shape only, local default); no RRF/embedding/rerank in query path default (FTS5 first per `fidx`/`qmd` latency evidence); no snapshot accept by agent (human-only per evals README); no `transforms: []` or `require_parameters` changes; no `.env` commits.

## 4. Behavior changed / cover / not-done (definition of done)

- Changed: `research()` becomes internal-first with compiled wiki, deduped sources, maintained vault+index; all behind ports, nodes stay pure.
- Covered by: new `internal_wiki_demo` case + existing `_demo`, `research`, `deep` replays staying green + contract suites + `test_research_store.py`, `test_body_storage.py`, golden-master prompt test.
- Not done: live re-record, snapshot accept, Supabase variant, semantic-search extra, UI/graph view. Those are follow-upsidecar, not this gate.

## 5. Rollback

Docs-only phase: delete branch + `0017-0021` + draft case. Code phase: revert single slice PR; reset engine to `checkpointer_kind=file, store_kind=memory, artifact_store_kind=local`; confirm `git status --porcelain research_store knowledge_base src evals/snapshots` clean except intended docs.

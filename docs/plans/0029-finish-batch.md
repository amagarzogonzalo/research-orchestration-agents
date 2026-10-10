# 0029 — Finish batch: offline hit-path proof + auto-block + knowledge graph + hybrid RRF + activation script

**Status:** Delivered · **Date:** 2026-10-09 · **Parent:** 0028 · **Branch:** `feature/llm-wiki-loop0`

## 1. Motivation

0028 left five items open. All five are now closed with deterministic, offline,
no-new-dependency implementations (the eval-snapshot and live-Supabase items
remain human-gated by repo rules).

## 2. What shipped

1. **Offline wiki-hit e2e** (`tests/e2e/test_wiki_hit_path.py`) — a real
   compiled research graph with a pre-filed exact-query wiki page and *live
   adapters that raise if called*; asserts the hit path renders the wiki answer
   with zero live search/scrape/LLM calls, and the miss path still runs.
   This is the offline complement to the `internal_wiki_demo` draft eval case.
2. **Auto-block after 5 rejections** (`FileWikiStore`): `reject_draft` counts
   rejections (`.blocked.json`); at `_MAX_REJECTIONS` the concept refuses
   approval and `compile_extraction` stops re-filing it; `unblock_draft` +
   `blocked_slugs` restore control. Matches kytmanov's five-strikes pattern.
3. **WikiGraph** (`infrastructure/wiki/graph.py`) — deterministic nodes,
   `[[wikilink]]` edges, backlinks, shared-thread (4-signal source-overlap)
   edges, and label-propagation communities (pure Python; no deps). Surfaces
   `hub_slugs` and `to_json()` for scripting.
4. **Hybrid RRF search** (`WikiSidecar.hybrid_search`) — Reciprocal Rank
   Fusion (k=60) of FTS5 BM25 ranks + scan term-overlap ranks; deterministic
   (both inputs sorted pre-fusion). `FileWikiStore.search_wiki` uses it when
   the sidecar exists, scan-scoring otherwise.
5. **Supabase activation script** (`bin/wiki_supabase_provision.py`) — creates
   the `wiki` bucket and prints the exact activation invocation; missing keys
   exit 1 with a clear message (no silently provisioning half-config).

## 3. Why these close the plan

Karpathy's pattern says: answers shouldn't disappear (done in 0028), review
shouldn't loop forever (auto-block), the wiki is a knowledge *graph*
(WikiGraph), search should scale without losing lexical recall (hybrid RRF),
and the cloud mirror must be one command away (provision script). The hit-path
proof satisfies the "zero live calls on repeat" contract that the draft eval
case will one day gate directly.

## 4. Gates

ruff/ruff-format/mypy(strict)/tach/semgrep/pydoclint green; `pytest tests/`
green (exit 0); evals 6/6; bench green; new scripts fail-loud/run clean.

## 5. Deliberately still open (human-gated by repo rules)

- Snapshot acceptance for `internal_wiki_demo` (needs a live record + human
  `--accept` — the agent never accepts snapshots).
- Real Supabase provisioning (needs your `SUPABASE_URL`/`SUPABASE_SECRET_KEY`;
  the script + live-marked test are ready and skip safely without them).

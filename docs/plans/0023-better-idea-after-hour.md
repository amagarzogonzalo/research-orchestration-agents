# 0023 — Better idea after 1 hour of online research: hybrid wiki + RAG with refine loop

**Status:** Accepted (supersedes naive wiki-only in 0018-0021 where they conflict) · **Date:** 2026-10-08
**Parent:** 0017-0022 · **Type:** synthesis after ~1h web research (20+ sources fetched)

> For humans: don't pick wiki OR search. Do both, each for what it is good at.
> Wiki connects ideas across papers and cites precisely. Smart search covers more
> ground cheaply. A refine loop fixes what the first draft dropped. All in one file.

## 1. What changed after the hour

Naive plan was wiki-only. Preregistered evidence (Cochran `arXiv:2605.18490`, 24 papers,
13 questions, 2 blinded judges) says grounded synthesis is three separate skills:

- connecting papers (wiki wins big, +6.6 on multi-hop/emergence),
- claim-by-claim citation support (wiki wins, survives decomp-RAG),
- cheap coverage + holistic groundedness score (RAG/decomp-RAG wins, wiki query
  cost 21x higher in that setup, no break-even).

Decomposition-RAG closes ~88% of the wiki synthesis gap cheaper. Huerta WiCER
(`arXiv:2605.07068`, 17 RepLiQA domains, 6800 Qs) adds: full-context KV beats RAG on
curated (4.38 vs 4.08, 7.3x faster TTFT) but degrades at scale via attention dilution;
blind compile fails 53-60% catastrophic; 1-2 compile-evaluate-refine rounds recover
80% and cut failures 55%. Targeted diagnosis (+0.95) beats generic pinning (+0.16).
Streaming follow-up adds regret-vs-oracle as the honest metric on evolving corpora.

So the better idea is hybrid with a refine loop, not wiki-only.

## 2. The better architecture (still files-first, zero-cost)

```text
raw/ (immutable) -> compile -> wiki/ (markdown, source of truth)
  + .wiki.db (single SQLite: documents|chunks|vec_chunks|fts_chunks|journal, WAL)
  + Memory OS tiers: /pad transient, /episodic auto-archived, /fact persistent
Query: index.md first -> SQLite hybrid (FTS5 BM25 + vector RRF k=60) -> wiki pages
  -> fallback decomp-RAG over raw when wiki misses -> file good answers back
Nightly: lint (deterministic) + WiCER probe (1-2 rounds, fact pinning) + prune cold
```

Pieces with sources:

- Wiki shape from GD4AI obsidian-llm-wiki (672 stars, zero-embedding graph retrieval,
  zero deps, Obsidian is runtime) + kennyg setup guide (Wiki/{sources,entities,
  concepts,synthesis} + index/log/overview + type/confidence/source_count/Dataview)
  + MehmetGoekce L1/L2 (L1 auto-loaded 10-20 rules, L2 wiki 50-200 pages on-demand,
  Access-Log with routing reason, LRU prune default 6mo).
- Memory from Aura Research/Memory OS v2.1 (WAL 0.001s writes, end_session compiles
  to shards, pad/episodic/fact, bloom+SimHash dedup zero RAM) + vstash/palinode pattern
  (chunks + access counters + MMR dedup + recency boost opt-in + profiles isolation).
- Search from sqlite-vec-benchmark (SciFact 5183 docs: hybrid nDCG 0.736 beats vec-only
  0.721 and FTS-only 0.681, p99 5.7ms, 24MB single file) + obsidian-hybrid-search
  (BM25 10x title / 5x alias + RRF 1.5x each + rerank) + lexa tiers (instant BM25
  250us / fast hybrid 9ms / deep question-form).
- Dedup tiers from 0019 hold, plus: MinHash beats SimHash on sparse binary (PMLR
  worst-case proof); production J>=0.7 standard; FOLD online HNSW bitmap keeps
  0.94-0.97 recall at 30M docs with 8x throughput for evolving wiki; SemDeDup
  k-means + epsilon cosine for semantic (keep farthest-from-centroid as keeper).
- LangGraph mapping holds (checkpointer thread-scoped + store cross-thread
  namespaces), plus security: upgrade `langgraph-checkpoint-postgres>=3.1.1` for
  GHSA-47pj namespace prefix leak (foo matches foobar, _/% unescaped). Use UUID
  namespaces where tenants matter. `setup()` once, cron-prune, thread_id<255.
- Evals: keep judge advisory. 21-judge audit (541k judgments): kappa deflation
  33-41pp universal, rank shifts up to 14 places, reliability >0.95 can coexist with
  position bias >0.10. So score synthesis and citation separately (they disagree:
  rho 0.04 holistic vs 0.81 claim-level), use prediction-powered inference
  (judge all N, human-calibrate small n, Rogan-Gladen/PPI correct).

## 3. Concrete delta to 0018-0021

- 0018 internal-first stays, but query becomes hybrid: index scan -> SQLite RRF ->
  wiki pages; miss triggers decomp-RAG (sub-queries fan-out like BranchNode) over raw,
  not single-round RAG.
- 0019 dedup stays, plus online FOLD path for streaming ingests + SemDeDup epsilon
  config (`eps_thresholds [0.01, 0.001]`, `which_to_keep: hard`) for semantic tier.
- 0020 storage stays markdown-first, DB is single `.wiki.db` derivable/deletable with
  the five-table shape above; add `journal_entries` for cross-session agent memory.
- 0021 slices gain S1b (decomp-RAG fallback) + S4b (WiCER probe: 1 QA per source doc,
  pin dropped facts, recompile max T=2) + eval split (claim-citation vs holistic).

## 4. TDD + gates (unchanged discipline)

Same `make check` gate. New fixtures: 24-paper-style mini corpus (reuse eval cassettes),
golden RRF fusion test (k=60), WiCER probe test (dropped fact reappears after refine),
namespace isolation test (foo vs foobar). No new pip dep in S0-S4 (stdlib sqlite3 FTS5,
vendored hashes); sqlite-vec/embedding/rerank stay opt-in extra behind `store` pattern.

## 5. Done criteria

- Changed: hybrid wiki+RAG with WiCER refine, single-file hybrid index, tiered memory.
- Covered by: `internal_wiki_demo` extended with synthesis-vs-citation split + existing
  `_demo/research/deep` replays green.
- Not done: live re-record, snapshot accept (human-only), Supabase variant, UI graph.

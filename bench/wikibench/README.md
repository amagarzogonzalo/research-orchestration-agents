# WikiBench — deterministic benchmark for the LLM wiki

Implements the Tier 0 core of the benchmark spec in
[issue #33](https://github.com/amagarzogonzalo/research-orchestration-agents/issues/33):
a fast, offline, no-LLM harness that measures **retrieval quality, miss
discipline, cost compounding, ingest integrity, lint detection, robustness and
the research/deep workflows** — so the wiki's behaviour is measured, not
asserted.

The framework is a first-class library at `src/knowledge_engine/wikibench/`
(contracts, metrics, corpus, gate, per-use-case suites), covered by
`tests/unit/wikibench/`. Only the printing CLI lives in
`src/knowledge_engine/scripts/wikibench.py`, so the library stays `print`-free.
This directory holds the committed baseline and generated reports.

## Use cases

Suites are grouped by the question a user asks; `--use-case` runs a whole group.

| Use case | Suite | Question |
|---|---|---|
| `answer-from-wiki` | `retrieval` | Does the right page come back? |
| `answer-from-wiki` | `miss` | Does it stay quiet, and route exact vs scored safely? |
| `compound-knowledge` | `cost` | Does it get cheaper over time? |
| `file-research` | `ingest` | Does it avoid duplicates, drift and spurious rewrites? |
| `maintain-wiki` | `lint` | Does it find its own defects? |
| `maintain-wiki` | `robustness` | Does it survive hostile input? |
| `run-research` | `workflows` | Do research, qa, summary and deep complete? |

## Commands

```
make wikibench         # run every Tier 0 suite, write bench/wikibench/report.{json,md}
make wikibench-check   # gate vs bench/wikibench/baseline.json (in `make check`)
make wikibench-accept  # adopt the current metrics as the baseline (review like code)
```

Raw CLI (supports `--suite`, `--use-case`, `--scale`, `--seed`, `--backend`,
`--tier`, `--json`, `--report`):

```
uv run python -m knowledge_engine.scripts.wikibench --use-case answer-from-wiki
uv run python -m knowledge_engine.scripts.wikibench --suite retrieval --scale 300
```

## Design

- **Planted facts, not real ones.** The corpus is fictional ("Vorlund protocol",
  "Kestrel-9 battery") so a model's training knowledge can never answer for the
  wiki; every gold answer traces to a specific seeded page.
- **Deterministic first.** Same `(seed, scale)` → byte-identical corpus and
  report; the timing-free suites replay bit-identically (asserted in tests).
- **Same suite on every backend.** Tier 0 runs the `file` backend today; the
  Supabase-fake, Postgres and staging backends are the next tiers (below).
- **No composite score.** Per-suite results only.
- **Compared against baselines.** The retrieval suite scores the scan ranker and
  the hybrid (FTS5 BM25 + scan RRF) ranker on the same corpus.

## One-page report (scale 10, seed 7, `file` backend)

### `answer-from-wiki` — retrieval quality

| Ranker | Recall@1 | Recall@3 | Recall@5 | Dup rate |
|---|---|---|---|---|
| scan | — | 1.00 | — | 0.00 |
| hybrid | 0.98 | 1.00 | 1.00 | 0.00 |

MRR 1.00, nDCG@5 1.00. Hybrid is never below scan; one page never occupies two
slots (the duplicate-page regression the audit fixed stays fixed).

### `compound-knowledge` — cost compounding (200-query stream)

| Metric | Value |
|---|---|
| zero-live-call rate, first query | 0.00 |
| zero-live-call rate, final | 0.95 |
| lift over the stream | +0.95 |
| live calls per 100 queries | 5 |

Each fact is filed once, then repeated; repeats exact-hit and spend zero live
calls. The rising zero-call rate is the compounding curve.

### Trust & integrity

| Use case | Metric | Value |
|---|---|---|
| answer-from-wiki (`miss`) | false-positive rate | 0.00 |
| answer-from-wiki (`miss`) | exact/scored classification accuracy | 1.00 |
| file-research (`ingest`) | verdict accuracy (NEW/NO_MATERIAL/UPDATE) | 1.00 |
| file-research (`ingest`) | idempotent-replay byte equality | 1.00 |
| file-research (`ingest`) | index drift / duplicate-page rate | 0.00 / 0.00 |
| maintain-wiki (`lint`) | clean-vault ok | 1.00 |
| maintain-wiki (`lint`) | per-class recall / precision | 1.00 / 1.00 |
| maintain-wiki (`robustness`) | injection / huge / unicode / index-complete | 1 / 1 / 1 / 1 |

### `run-research` — workflow coverage

| Workflow | Supersteps | Artifact |
|---|---|---|
| research | 13 | ok |
| qa | 9 | ok |
| summary | 10 | ok |
| deep | 15 | ok |

These match the committed perf baseline (`bench/baseline.json`) exactly — the
benchmark adds measurement without changing engine behaviour.

## Latency profile (evidence, not a gate)

Retrieval latency grows with vault size because the hybrid ranker fuses FTS5
with a full-vault scan (`WikiSidecar.hybrid_search` → `_scan_scores`):

| Scale (pages) | p50 (ms) | p95 (ms) | Recall@3 | Dup rate |
|---|---|---|---|---|
| 10 | 10.2 | 11.8 | 1.00 | 0.00 |
| 100 | 60.8 | 82.4 | 1.00 | 0.00 |
| 300 | 171.9 | 220.4 | 1.00 | 0.00 |

Correctness is flat; only latency scales. This is a documented future
optimisation target (bounded scan, or FTS-only candidate generation with scan
as a tie-breaker) — deliberately **not** changed here, because altering the
ranker would move wiki behaviour that the audit's regression tests pin.

## Execution tiers (roadmap)

- **Tier 0 (implemented)** — retrieval, miss, cost, ingest, lint, robustness,
  workflows on the `file` backend. No LLM; sits in `make check`.
- **Tier 1** — provenance/faithfulness, contradiction, temporal and
  synthesis/multi-hop suites with a scripted fake provider.
- **Tier 2** — the same suites with live free models; trend only, never a gate.
- **Tier 3** — backend parity across Supabase-fake/Postgres and the staging
  deployment (issues #30/#31), including concurrent-write coherence.

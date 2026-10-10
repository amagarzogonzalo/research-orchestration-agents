# Evals — behavioral regression for LLM-driven code

Record/replay over cassettes: `make evals` (replay) is offline, free, and
deterministic; `make evals-live` re-records against live APIs (spends quota —
ask first). The judge is advisory; deterministic layers (invariants, budgets,
snapshots) are the gate.

## Cases

| Case | Query | Exercises |
|---|---|---|
| `_demo` | toy | Harness smoke test, always green |
| `research` | RAG/hallucinations | Full research workflow |
| `deep` | RAG vs long-context | Recursive deep workflow, fan-out |
| `biomed` | CRISPR off-target | Biomedical adapters |
| `web` | RAG general knowledge | General-web tier (Wikipedia, DuckDuckGo, Tavily, Exa) |
| `internal_wiki_demo` | RAG/hallucinations | Full pipeline on the canonical wiki query (active; the wiki hit path itself is covered offline by `tests/e2e/test_wiki_hit_path.py`) |

Each case: `entrypoint`, `input`, `samples`/`pass_at_k`, `invariants`
(frontmatter, sections, citations, no-uncited-claims, valid links, max_chars),
`budget` (tokens/calls/wall), `judge` (rubric, advisory), optional
`deepeval:` metric block.

## Commands

```
uv run python evals/runner.py                 # replay, offline, free
uv run python evals/runner.py -k research     # one case
EVAL_MODE=record uv run python evals/runner.py --record -k research   # re-record ($)
uv run python evals/runner.py --accept        # adopt digests (HUMAN ONLY)
uv run python evals/runner.py --deep          # + DeepEval pass (opt-in extra)
```

`make evals` / `make evals-live` / `make evals-accept` / `make evals-deep`
wrap the same.

## The loop (follow exactly)

1. **Wipe the case dir first** (`evals/cassettes/<case>/run0/`). The runner
   refuses dirty dirs (`--force` overrides). Reason: record overwrites on
   success but preserves files for live-failed calls, so a later replay hits
   stale files and diverges — proven twice by Ray-ID/bot-page diffs.
2. **Record once.** Within a run, duplicate identical calls reuse the first
   live result (record memo) so files always match the prompts built from
   them — per-request IDs and timestamps can't desync.
3. **Replay immediately.** It must reproduce bit-identically (same tokens).
   If it doesn't, the world is inconsistent — do not accept, investigate.
4. **Review, then accept.** `--accept` adopts digests as the new baseline.
   Review the BREAKING diff like a code diff: dropped citations (same paper
   under a new DOI form is fine), removed/added sections, count shifts.
   Never accept to silence red; never raise a budget to fit (rule 6).

## Budgets, judges, metrics

- Budgets (`max_total_tokens`/`calls`/`wall_s`) are cost guards calibrated
  from measurement. If a deliberate upgrade (more evidence) overflows one,
  fix the cost structurally (evidence caps, fan-out bounds) — do not raise.
- The judge (`qwen`→`gemma` history: free-tier slugs die; re-pin + verify)
  and DeepEval metrics are advisory until κ-calibrated. `answer_relevancy`
  0.7 is validated (1.00 real vs 0.00 mismatched control); `faithfulness`
  checks internal support, not topicality.
- `citation yield` (sources actually footnoted over retrieved) prints per
  case as a precision signal for the relevance loop. Low yield + clean
  invariants = working pipeline on a noisy corpus, not a failure.

## Troubleshooting

- `no recording for .../scrape/...`: live scrape failed during record
  (bot walls, 403s) — tolerated misses. Only re-record if the SET changed.
- `judge recording missing` after a record: the run died at the judge
  (free-tier 429). Recover surgically: replay regenerates the artifact
  deterministically, then record just the judge call (see session notes).
- `InvalidUpdateError: At key 'status'`: concurrent branch failures racing
  error writes — fixed by last-wins reducers; if it recurs, a branch is
  missing cassettes (re-record clean).
- Reasoning-model substitution (empty librarian verdicts, `finish: length`):
  OpenRouter may route the cheap slot to a reasoning model that deliberates
  past the token budget. Batching + slim ledgers bound it; keep-all degrade
  + bodies cap bound the cost. Do not chase with prompt tweaks.
- `EVAL_MODE=record` leaked in `.env` flips every runner invocation to
  record mode. Keep it unset; the runner takes `--record` explicitly.
- Playwright browsers must match the installed playwright build
  (`python -m playwright install chromium` after upgrades) or Crawl4AI dies.

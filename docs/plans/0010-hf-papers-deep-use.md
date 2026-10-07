---
@id: "plans/0010"
type: ADR
status: PROPOSED
date: 2026-10-01
related:
  - 0008   # LangGraph orchestrator (nodes/graphs this builds on)
  - 0009   # portable library, files-only, free-LLM discovery
---

# 0010 — Papers-With-Code, Used Strongly: HF Papers Deep Integration

## Context

The Papers With Code API is retired (verified live: `paperswithcode.com/api/v1/*`
→ 302 to HuggingFace). Its verified successor is the **HuggingFace Papers API**
(all keyless, all live-probed 2026-10-01). This plan turns that API from "one
more search source" into the library's strongest relevance signal.

## 1. Verified tool surfaces (all live-probed, no recalled knowledge)

| Tool | Endpoint / capability | Verified fact |
|---|---|---|
| HF search | `GET /api/papers/search?q=` (≤250 chars) | 200, 118 hits for "attention"; hybrid semantic+fulltext; per-item `upvotes`, `numComments` |
| HF paper detail | `GET /api/papers/{arxiv_id}` | 200; `ai_summary`, `ai_keywords`, `organization`, `discussionId`, `upvotes`; `linkedModels/Spaces` usually null |
| HF daily | `GET /api/daily_papers` (+ `date/week/month`, `submitter`, `sort`, `limit` params per OpenAPI) | 200, 50 items; richer items (`submittedBy`, `organization`, `mediaUrls`) |
| HF hub filter | models/datasets/Spaces filtered by `arxiv:{id}` | documented in Hub OpenAPI; not yet probed — verify before use |
| GitHub API | `GET /api.github.com` with digest token | 200, **5000/hr** quota; enables repo/star validation for code linkage |
| OpenAlex | `search=` param, DOI-first fetch | fixed earlier; polite-pool via mailto |
| arXiv export API | Atom feed | working; HTML abs pages scrape cleanly via trafilatura |

## 2. What we can do with this (ranked by value/cost)

1. **Enrich stage (highest value).** After ingest, for every source with an
   arXiv ID, fetch `/api/papers/{id}` and attach `ai_summary` (free abstractive
   summary — saves synthesis tokens), `ai_keywords`, `upvotes`, `organization`.
   New `EnrichNode` (pure `(State)->State`, I/O via `infrastructure/`), or a
   post-ingest function if the graph should stay 8 nodes. Cost: ~1 keyless call
   per paper, cacheable in `research_store/`.
2. **Upvote-weighted relevance.** Feed `upvotes` into the relevance/judge
   scoring as a community prior (trending-validated > keyword-matched). Keeps
   selection deterministic (no LLM judging).
3. **Trending mode.** `daily_papers` (+ date/sort params) as an ingest source
   for "what's hot" queries; new `trending` workflow reusing the same nodes.
4. **Code linkage (opt-in, needs token).** Resolve each paper's code repo
   (GitHub search by arXiv ID), record stars; surfaces the literal
   "papers WITH CODE" signal. Token-gated, off by default; 5000/hr is plenty.
5. **Already shipped:** `HuggingFacePapersAdapter` in the default demux
   (upvote-ranked, keyless), wired in `engine._default_search()`.

## 3. Build phases (small, reviewable)

- **P1 — enrich + wire:** `EnrichNode` + `infrastructure/search/huggingface_enrich.py`
  (detail fetch w/ file cache), unit tests with stubbed transport, live-probe once.
- **P2 — scoring:** upvote prior in relevance/judge (pure functions, unit-tested).
- **P3 — trending workflow:** `daily_papers` ingest + `trending` graph reusing nodes.
- **P4 — code linkage (opt-in):** GitHub repo resolution behind `GITHUB_TOKEN`;
  skipped cleanly without a token.

Rules honored: no new dependency (httpx only), no new MCP server, zero-cost
(keyless HF; token-gated GitHub), Google docstrings, strict mypy on new code.

## 4. Eval coverage

- Offline unit tests with stubbed transport (pattern already established in
  `tests/unit/test_huggingface_papers.py`).
- New `evals/cases/hf_papers.yaml` replaying a recorded detail payload through
  the enrich scoring (offline replay per the evals runner contract).
- Live verification is manual (`RUN_LIVE_E2E=1`), never in CI.

## 5. What this does NOT do

- No vector DB, no embeddings, no paid reranker — community + LLM-judge only.
- No browser scraping of HF pages — API only (cheaper, stable).
- `linkedModels/Spaces` are usually null; not relied upon until verified richer.

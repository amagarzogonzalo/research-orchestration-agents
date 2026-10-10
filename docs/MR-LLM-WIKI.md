# MR — LLM Wiki layer on the research orchestration agent

**Branch:** `feature/llm-wiki-loop0` → `main` (clean fast-forward)
**Status:** all gates green · live-verified against real Supabase + free-tier LLMs
**Full plan:** `docs/LLM_WIKI.md` · **Delivery reports:** `docs/plans/0017–0030`

---

## TL;DR (for humans)

The engine used to be a robot that re-researched everything from scratch on
every question. This MR adds a **persistent, self-maintained wiki** in the
style of Andrej Karpathy's *LLM Wiki* pattern. Now:

- the agent **checks its own wiki first** before spending any live API quota;
- **whatever research it finds is what gets ingested** into the wiki;
- the wiki is deduped, indexed, logged, lintable, searchable, and graphable;
- all the LLM "wiki compiling" is **opt-in** and **degrade-safe**;
- nothing leaves the zero-cost free-tier discipline.

## The core mapping: research output IS the wiki's input

This is the heart of the design, stated plainly:

```
        RESEARCH (what the orchestrator finds)              WIKI (what the agent maintains)
  ┌───────────────────────────────────────────┐     ┌─────────────────────────────────────────┐
  │ research_store/<thread>/                  │     │ <store_root>/wiki/                       │
  │   article.md      ← synthesized report    │──►  │   sources/<slug>.md   ← that report      │
  │   sources.json    ← the papers/pages found│     │                         filed as a page  │
  │   metadata.json   ← query/models/status   │     │   concepts/  entities/  synthesis/       │
  │   events.jsonl    ← trace / audit         │     │   queries/                               │
  │ research_store/bodies/<sha>.md            │     │   index.md  log.md  overview.md          │
  │   (immutable scraped full text)           │     │                                          │
  └───────────────────────────────────────────┘     └─────────────────────────────────────────┘
          RAW (immutable, never edited)                COMPILED (LLM-owned, maintained)
```

So, concretely:

- **Raw sources = the research the repo performs.** Fetched scholarly/web
  sources (`sources.json`), scraped full-text bodies (`bodies/<sha256>.md`),
  and the rendered article (`article.md`) are the immutable layer. They are
  the source of truth and are never edited by the wiki.
- **Ingested content = that same research output.** Every finished run is filed
  into the wiki as a source page (`wiki/sources/<slug>.md`) that carries
  `thread`/`sha256`/`query_sha256` lineage straight back to the raw run.
- **Compiled layer = what the LLM adds on top** (opt-in): concept/entity pages
  distilled from the source page, thesis verdict pages, and filed answers.
- The `index.md` catalog and `log.md` diary are maintained automatically.

One sentence: **the orchestrator does the research; the wiki is where that
research is ingested, deduped, cross-linked, and kept current.**

## The problem it solves

Classic RAG re-derives knowledge every query: the same papers are fetched
again, the same synthesis is redone, and the same reports pile up with no
memory between sessions. This MR stops that: knowledge is compiled once and
kept current.

## The idea (Karpathy's LLM Wiki, in one breath)

Three layers and three operations:

- **Raw sources** (immutable) → **the wiki** (LLM-owned markdown) → **schema**
  (a contract file that keeps the agent disciplined).
- **Ingest** (read a source → update many pages), **Query** (answer from the
  wiki), **Lint** (health-check contradictions, stale claims, orphans, gaps).
- Two files carry it: `index.md` (catalog) and `log.md` (append-only diary).

## How it lands on this orchestrator

Every workflow (`research`, `qa`, `summary`, `deep`) is wrapped:

```
question ─► wiki_query ── hit ──► render   (answer from the wiki; ZERO live calls)
                 │
                miss
                 ▼
   ingest → clean → relevance/bodies → synthesize → judge → render   ← the existing orchestrator
                 │
                 ▼
              wiki_ingest   (file this run's research into the wiki) ─► END
```

- **`wiki_query`** — exact-query hit answers straight from the wiki with no
  live search/scrape/LLM. Miss = unchanged pipeline (byte-identical to before).
- **`clean`** — dedups the retrieved batch (close-never-delete).
- **`wiki_ingest`** — files the run's research back into the wiki and, if
  opted in, runs the LLM compile.

## Features

**Wiki core** — markdown vault (`sources/ concepts/ entities/ queries/
synthesis/` + `index.md log.md overview.md`); verdicts NEW/UPDATE/NO_MATERIAL;
SHA cache; `WIKI_SCHEMA.md` + `purpose.md`. Portable: move the folder, move the wiki.

**Query** — `await engine.wiki_query("topic")` (no live call) and the automatic
wiki-first behaviour inside `research()`.

**Cleaner** — exact-id dedup, MinHash+LSH+union-find near-dup, containment,
optional DOI dedup, and **cross-thread content dedup** at the wiki layer
(measured: 21 cross-thread DOIs, 0 intra-batch).

**LLM enrichment (opt-in via `RunSettings`)**

- `wiki_extract=True` → two-step compile into concept/entity **drafts**.
- `wiki_save_answers=True` → file the rendered answer under `queries/`.
- `thesis="claim"` → for/against **verdict page** under `synthesis/`.
- `max_wiki_pages=N` → cap pages per ingest.
- Draft review: approve (hand-edit hash guard) / reject (feedback into next
  compile) / **auto-block after 5 rejections** / unblock.
- **Degrade-safe:** a flaky free-tier reply never sinks the run; the source
  page is guaranteed and a `wiki_extract_error` note is recorded. The schema
  gate refuses placeholder structure, so nothing poisonous lands.

**Storage** — local files (default), **Supabase mirror** (verified live), and
a derived **`.wiki.db` FTS5 sidecar** with **hybrid RRF search**.

**Health & tooling** — deterministic lint, **LLM cross-page lint**
(contradictions/stale/gaps), **knowledge graph** (nodes, wikilinks, backlinks,
shared-thread edges, communities).

**Eval** — `internal_wiki_demo` is an active gating case (recorded live,
replayed bit-identical, snapshot accepted); the hit path is gated offline by
`tests/e2e/test_wiki_hit_path.py`.

## How to use

```python
import asyncio
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.schemas.settings import RunSettings


async def main() -> None:
    engine = ResearchEngine()                    # wiki at research_store/wiki (files-only)

    # 1) Normal research — the result is ingested into the wiki automatically.
    result = await engine.research("state of the art in multi-agent orchestration")
    print(result.state.output_path)              # research_store/<thread>/article.md

    # 2) Ask the wiki directly (no live call).
    hits = await engine.wiki_query("multi-agent orchestration")
    print([(h.slug, h.score) for h in hits])

    # 3) Opt in to the LLM wiki compile for a run.
    await engine.research(
        "how retrieval augmentation reduces hallucinations",
        settings=RunSettings(
            wiki_extract=True,
            wiki_save_answers=True,
            thesis="Retrieval augmentation reduces hallucinations.",
            max_wiki_pages=8,
        ),
    )


asyncio.run(main())
```

**Cloud mirror:**
```python
engine = ResearchEngine(wiki_store_kind="supabase")   # SUPABASE_URL + SUPABASE_SECRET_KEY
```
Verify once: `uv run python bin/wiki_supabase_provision.py`

**Commands:**
```
make wiki-lint        # deterministic health gate
make wiki-lint-deep   # + opt-in LLM contradictions/stale/gaps
make wiki-review      # list drafts; accept/reject via bin/wiki_review.py
make wiki-graph       # knowledge graph JSON
make check            # full gate
```
Helpers: `bin/wiki_review.py`, `bin/wiki_graph.py`, `bin/wiki_lint.py`,
`bin/wiki_lint_deep.py`, `bin/clean_report.py`, `bin/wiki_supabase_provision.py`.

## Why "wiki-first" saves money

Cheap models are not cheap when they loop. A repeat query that hits the wiki
spends **zero** live calls; a miss costs exactly what it did before. The wiki
compounds instead of the bill.

## Tests, evals, gates

- Contract suites across fake/file/Supabase adapters (LSP proof).
- Offline e2e proves the hit path is live-call-free and that qa/summary/deep compile.
- `make evals` 6/6 green; bench re-baselined (documented); ruff, format,
  mypy strict, tach, semgrep, pydoclint, agents, context all green.

## Known limits (honest)

- The hit shortcut fires on an **exact query match**; fuzzy matches are
  recorded for observability but still run the live pipeline.
- LLM enrichment is opt-in; the deterministic wiki works with no keys.
- Supabase activation is one setting away (keys in `.env`, bucket verified
  live). The agent deliberately does not push `main`.
- Diagrams show the search→render spine; the wiki wrapper is documented in
  `docs/diagrams/README.md`.

## Merge

`origin/main` is an ancestor → clean fast-forward. Local `main` is already
fast-forwarded and verified. To publish: `git push origin main` (human step).

## Mental model in one line

**The research orchestration agent finds the content; the LLM wiki is where
that research is ingested, deduped, compiled, and kept current.**

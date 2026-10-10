# 0024 — Rephased plan: clean anything, Supabase-first research, iterative Karpathy wiki

**Status:** Proposed · **Date:** 2026-10-09 · **Type:** architecture + delivery
**Rephrases:** 0017-0023 (which were paper-centric) for any data type
**Evidence:** Karpathy gist `442a6bf555914893e9891c11519de94f` (fetched 2026-10-09, 5000+ stars) + thread comments + `karpathy-wiki.lol`, `llmwiki.cc`, `deanjstone` synthesis, `SuperCodeAgents/SCHEMA.md`

## 0. Your three goals, rephrased (not only papers)

1. **Clean useless data** — any junk: duplicate PDFs, re-clipped articles, stale transcripts, bot-wall HTML, superseded notes, orphan bodies, failed scrapes. Not a papers-only deduper.
2. **New research looks into Supabase first** — before spending quota on live search/scrape/LLM, check cloud memory: Supabase Storage (`articles`, `sources`, `bodies` buckets) + Postgres store/checkpointer. Local files are the offline mirror, Supabase is the shared truth.
3. **Iteratively build a Karpathy LLM wiki** — raw immutable, LLM-owned wiki, schema contract; ops ingest/query/lint; `index.md` + `log.md`; compounding, any domain (notes, tickets, transcripts, ADRs, trip plans, not just papers).

## 1. Evidence from the gist (why this shape)

- **Core idea (gist: The core idea):** RAG re-discovers from scratch every query (NotebookLM, ChatGPT uploads). Wiki compiles once and keeps current. Cross-refs already there, contradictions flagged, synthesis reflects everything read. This is the compounding artifact.
- **Three layers (gist: Architecture):** raw immutable source of truth (articles, papers, images, data files — extend to any file: md, tickets, transcripts, Slack) / wiki directory LLM owns entirely (summaries, entities, concepts, comparisons, overview, synthesis) / schema doc (`AGENTS.md`/`CLAUDE.md` here) that makes the LLM a disciplined maintainer. You co-evolve it.
- **Ingest (gist: Operations):** drop source -> read -> discuss takeaways -> summary page -> update 10-15 entity/concept pages -> update index -> append log. One-at-a-time with human involved, or batched. Record choice in schema.
- **Query (gist):** search relevant wiki pages, synthesize with citations (page, table, Marp deck, chart, canvas). **File good answers back as new pages** — comparisons/analyses/discoveries compound like ingests.
- **Lint (gist):** periodic health-check: contradictions, stale superseded claims, orphans with no inbound links, concepts mentioned without pages, missing xrefs, gaps fillable by web search. Suggests next questions/sources.
- **index.md (gist: Indexing):** content catalog, link + one-line summary + date/source-count, by category. Updated every ingest. Read first on query. Enough to ~100 sources / hundreds of pages with no embeddings.
- **log.md (gist):** append-only `## [YYYY-MM-DD] ingest | Title`, parseable via `grep "^## [" log.md | tail -5`. Timeline of evolution.
- **CLI tools (gist: Optional):** `qmd` hybrid BM25/vector + rerank, CLI + MCP, only when index saturates. Vibe-code naive search first.
- **Tips that map to this repo:** Obsidian Web Clipper -> our `ScrapeRouter` intake; download images to `raw/assets/` -> our bodies bucket naming; graph view -> backlink/orphan lint; Dataview over frontmatter -> require YAML frontmatter on wiki pages; Marp -> RenderNode output variant; git repo -> version history free (keep markdown diffable, per `crajah` thread: markdown wins when humans read).
- **Why it works (gist):** bookkeeping (xrefs, summaries, contradiction notes, consistency) is what kills human wikis; LLMs touch 15 files without boredom. Human curates/sources/asks, LLM does the rest. Memex (1945) associative trails, maintenance solved.
- **Thread evidence applied:** write-time conflict resolution (predicate groups, order-by-document not extracted dates, close-never-delete, valid-time vs belief-time two clocks; LongMemEval 94.0 vs 71.2, temporal +38.4 by rendering validity into prompt) / deterministic write gates (drop malformed, no placeholder poisoning) / computed index via graph clustering (nesting guaranteed) + gaps via retrieval counters (hash queries, not text) / log retrieved-vs-prompt chars (assembler drop = 8pts invisible) / fail-open capture (preserve raw on stage failure) / explicit wiki-consult triggers + `LESSON:` in tool descriptions (orcosto-lab 9-session audit) / tickets->meeting-notes->ADRs->Kanban compounding (kaimys Dispatch plugin) / Curated Thoughts MCP 16 tools + opencode install script as integration model.

## 2. Design for THIS repo

### 2a. Clean useless data (any type)

Location: new `infrastructure/clean/` + pure `CleanNode` before ingest, behind `CleanPort`. No new pip dep (stdlib `hashlib`, `sqlite3` FTS5 only).

Tiers, cheap -> expensive, all file types (md, pdf-extracted md, html, transcripts, json):

1. **Exact:** SHA256 bytes + DOI/URL key + frontmatter `id`. Drop byte-identical + empty (<50 chars) + known bot-wall markers (403/captcha/paywall strings). Keep first, link rest via `superseded_by`.
2. **Near:** SimHash 64-bit Hamming<=3 (short) + MinHash 128 perm Jaccard>=0.6 candidate / 0.8 merge + LSH + union-find transitive clusters. Suffix-array containment for parent/child (review vs chunk). Normalizes lowercase + strips chrome.
3. **Semantic (candidates only):** embedding cosine>=0.8 dup, 0.6-0.8 LLM adjudicate with grounding invariant (locate-before-write, grep high-signal literals). <1% pairs reach LLM.
4. **Stale:** `lnk stale`-style: path mentioned in wiki but missing on disk + git history shows rename -> flag with successor; otherwise prose, don't flag (precision over noise).

Policy: close-never-delete (mark `status: superseded|archived` + timestamp, keep retrievable), two clocks (`valid_time` world, `belief_time` system), order-by-document (ingest/thread order, robust to backfill).

### 2b. Supabase-first new research

Order on every `research(query)`:

```text
1. Wiki index (local knowledge_base/wiki/index.md, then Supabase mirror if enabled)
2. Supabase Storage: articles/<topic>.md? sources/<topic>.json? bodies/<hash>.md?
   + Postgres store namespaces ("wiki", topic) + checkpoints for resumable thread
3. Local research_store/<thread>/ (offline mirror, byte-identical)
4. Miss -> live federated search/scrape (existing Demux + ScrapeRouter), then file back
```

Implementation: new `WikiQueryNode` + `SupabaseLookupAdapter` (wraps `SupabaseArtifactStore.get_article`, `SupabaseBodyStorage`, `build_store("postgres")` search) behind `WikiQueryPort`. Fits `engine.py` composition root; nodes stay pure. Respects existing kinds (`artifact_store_kind`, `body_store_kind`, `store_kind`, `checkpointer_kind`); local-only still works with zero keys. Thread_id<255, `setup()` once, cron-prune checkpoints.

Triggers (from orcosto-lab): schema rule — on external-tool failure signature + first hypothesis failed -> check wiki/Supabase before second attempt; plus `LESSON:` one-liners in owned tool descriptions for failures that already bit.

### 2c. Iterative Karpathy wiki (any domain)

Layout (extends `knowledge_base/`):

```text
knowledge_base/
  raw/            # immutable intake (any type), never edited, gitignored per user
  wiki/
    index.md      # catalog: link + summary + updated + source_count, by category
    log.md        # append-only ## [YYYY-MM-DD] <op> | <title>
    overview.md   # evolving thesis
    sources/      # one summary per ingested source
    entities/     # people/tools/orgs/repos/places
    concepts/     # ideas/patterns/techniques
    comparisons/  # query answers filed back
    synthesis/    # deep answers filed back
  WIKI_SCHEMA.md  # the schema layer (structure, naming kebab-case, workflows, verdicts)
```

Conventions: `type:` frontmatter (source/entity/concept/comparison/synthesis), `wiki/` tag namespace, heavy `[[wikilinks]]`, `date_updated`, `source_count`, `[key::value]` inline for Dataview, `confidence: high|medium|low`. Verdicts per ingest: New/Update/Disputed/No-material (log-only on no-material).

Ops cadence: ingest one-at-a-time involved (default) or batched (documented in schema); query files back substantial answers; lint weekly (deterministic: orphans/broken/stale/missing + LLM: contradictions/gaps/new questions). Seed with 2-3 rich sources to calibrate templates.

## 3. SOLID + TDD (slices, `make check` green each)

- S0 ports+fakes+contracts: `CleanPort`, `WikiQueryPort`, `WikiIngestPort`, `VaultLintPort` in `core/ports.py`; fakes in `tests/conftest.py`; contract suites parametrized fake+file (+supabase when keys present, `@pytest.mark.live`).
- S1 cleaner: golden pairs (exact/near/paraphrase/containment/distinct) + `minhash-dedupe`-style report counts; RED first.
- S2 Supabase-first query: hit short-circuits live-search spy; miss bit-identical to today; stale-check precision test (0 false flags on 108-ref style fixture).
- S3 wiki iterate: migration goldens thread->wiki pages + index/log diffs; grounding invariant test (high-signal literals present in linked raws); two-clock query param test.
- S4 lint+eval: deterministic lint JSON (broken/orphan/frontmatter/thin/index-drift) + retrieval-counter gap test (hash, not text) + prompt-chars-reaching-model log test.
- Eval: extend `evals/cases/internal_wiki_demo.yaml` with wiki-hit path + lineage + index/log invariants; keep judge advisory; replay bit-identical before human `--accept`. Never raise budgets.

## 4. Rollout + rollback

Branch per slice; merge S0->S1->S2->S3->S4. Rollback: revert slice PR, reset engine to `file|memory|local`, `git status --porcelain research_store knowledge_base src evals/snapshots` clean except intended docs. No `.env` commits, no new MCP/dep without asking (AGENTS rules 1/4), free tiers only (rule 3).

## 5. Done lines

- Changed: any-type cleaner + Supabase-first lookup + iterative Karpathy wiki (raw/wiki/schema, ingest/query/lint, index/log) mapped to this repo's stores.
- Covered by: extended `internal_wiki_demo` + existing `_demo/research/deep/biomed/web` replays + new contract/lint/grounding tests.
- Not done: live re-record, snapshot accept (human-only), semantic-search extra on by default, Supabase bucket provisioning, UI/graph work.

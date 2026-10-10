# 0018 — Loop 1 v1: internal-first query + minimal LLM wiki (Karpathy pattern mapped)

**Status:** Proposed · **Date:** 2026-10-08 · **Type:** architecture + delivery
**Parent:** 0017 · **Related:** 0008, 0009, 0012, 0013
**Online set (fetched):** Karpathy `llm-wiki` gist (raw fetch 2026-10-08), DataCamp LLM Wiki explainer, `karpathy-wiki.lol`, `llmwiki.cc/docs`, `ddsyasas/llm-wiki`, `nashsu/llm_wiki` guide, `Astro-Han/karpathy-llm-wiki` SKILL.md, `Aura Research`, WiCER `arXiv:2605.07068`, Cochran single-round RAG vs wiki, Lanham write-path synthesis, RedHat memory architecture

> For humans: today the engine is like a student who re-reads the whole library
> for every question. The wiki turns it into a student who keeps good notes.
> Raw reports stay untouched, the wiki holds the distilled notes, and one rulebook
> tells the assistant how to keep notes tidy. Next time you ask, it checks notes first.

## 1. What online research forces (delta vs naive internal search)

Naive idea was "check old articles before searching". Pattern says more:

- Three layers only: **raw immutable** (`research_store/<thread>/` + `knowledge_base/bodies/`, never edited) -> **wiki** (`knowledge_base/wiki/`, LLM-owned) -> **schema** (one contract doc, co-evolved). Human curates, LLM maintains.
- Compilation-first at **ingest**, not retrieval-first at query. Each ingest is a refactor pass touching 10-15 pages: new entity/concept pages, revise summaries, flag contradictions on-page, refresh `index.md`, append `log.md`. Query reads wiki first, then files valuable answers back into `wiki/queries/`.
- Two special files carry moderate scale (~100 sources, hundreds of pages) with no embeddings: `index.md` (catalog: link + one-line summary + date/source-count, grouped by entities/concepts/sources) read first on every query; `log.md` append-only `## [YYYY-MM-DD] <op> | <title>` parseable via grep. `ddsyasas` adds source lineage both directions + index integrity rebuild; `Astro-Han` adds grounding invariant (locate-before-write, `scripts/check_evidence.py` grep of high-signal literals) + four ingest verdicts New/Update/Disputed/No-material.
- `qmd` only when index saturates: SQLite + FTS5 BM25 first, then hybrid BM25+vector RRF k=60 + rerank, plus MCP server. WiCER result explains why: full-context KV-cache beats RAG on curated knowledge (4.38 vs 4.08, 7.3x faster TTFT) but degrades at scale via attention dilution; blind compilation fails 53-60% without iterative refine. So v1 stays index-file-first, adds `qmd`-style search later.
- Write path matters (Lanham, RedHat, Cochran): persistent editable context + episodic ledger + shared vs agent-scoped LTM beats stateless RAG for long-horizon agents. Cochran preregistered result: wiki agent vs single-round RAG is a multi-axis comparison (offline rewrite + markdown + tool-loop), judge accordingly.

## 2. Proposal v1 for this repo

- New `knowledge_base/wiki/<slug>.md` pages with YAML frontmatter (`id`, `title`, `type`, `tags`, `sources`, `updated`, `tldr_llm`), `[[wikilinks]]`, Obsidian-compatible. `wiki/index.md` + `wiki/log.md` per above. `WIKI_SCHEMA.md` as the schema layer (structure, naming kebab-case, ingest/query/lint workflows, conflict annotation rule).
- New ports in `core/ports.py`: `WikiQueryPort.search_wiki(query, limit) -> list[WikiHit]` and `WikiIngestPort.ingest(source, hits) -> WikiUpdate`. Adapters in `infrastructure/wiki/`: `FileWikiStore` (markdown + index/log, no new dep) today; `SqliteWikiIndex` (FTS5 sidecar, stdlib sqlite3) later; Supabase variant only if project allows (see 0021 open point).
- New pure nodes: `WikiQueryNode` before `IngestNode` (hit -> cite wiki + skip or narrow live search; miss -> proceed + mark gap), extended ingest-to-wiki step after `RenderNode` (or async post-run) that applies New/Update/Disputed/No-material verdicts. No node does I/O except through ports.
- Query flow: `WikiQueryNode` reads `index.md` first, drills into top pages, synthesizes with citations; on miss runs existing `DemuxSearchAdapter` chain unchanged. Every valuable answer archived to `wiki/queries/`.

## 3. SOLID mapping

SRP: query vs ingest vs lint are separate nodes/ports. OCP: new backends are new adapter files, `ResearchStore` untouched. LSP: one contract suite runs against every wiki adapter. ISP: ports expose `search/ingest/lint` only. DIP: nodes depend on ports, only `engine.py` chooses `FileWikiStore`.

## 4. TDD slices (red->green, `make check` green each)

0. Contract `tests/unit/wiki/test_wiki_store_contract.py` parametrized over fake+file adapters: put/get roundtrip, index refresh, log append, missing returns None. RED first.
1. `WikiQueryNode` unit with fake store: hit short-circuits search spy (assert live search not called), miss calls through.
2. Ingest verdict unit: New/Update/Disputed/No-material matrix on fixtures; Disputed flags on-page, never silent overwrite.
3. Integration with recorded cassettes: wiki-hit replay uses zero live calls; wiki-miss replay bit-identical to today.
4. Eval draft `evals/cases/internal_wiki_demo.yaml` (see 0021): internal-hit citation + freshness + lineage invariants.

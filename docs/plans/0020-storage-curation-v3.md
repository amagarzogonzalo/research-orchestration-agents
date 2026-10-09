# 0020 — Loop 3 v3: nicer storage + curation (vault health + LangGraph memory mapping)

**Status:** Proposed · **Date:** 2026-10-08 · **Type:** architecture + delivery
**Parent:** 0019 (delta over v2 pipeline-only) · **Related:** 0012, 0013, 0017
**Online set (fetched):** LangGraph persistence/checkpoint/stores docs + `langgraph/libs/checkpoint/README`, `langgraph-checkpoint-postgres` usage (autocommit+dict_row, `setup()`), vault health set (`wortmanb/obsidian-vault-maintenance`, `avjcodes/vaultlint`, `ipshing/obsidian-broken-links`, `pmmvr/obsidian-index-service` SQLite watcher, `VaultQuery` SQLite, `turbobeest/obsidian-headless-cli`, `vault-mcp` Markdown+SQLite+FST5+wikilinks MCP, `fidx` hybrid RRF no-LLM-in-path, `qmd`/`notes-rag` BM25+sqlite-vec+RRF+rerank)

> For humans: files alone are a pile, a vault is a shelf with labels. Every note
> gets a title card (frontmatter), links both ways, an index at the front and a
> diary at the back. A tiny database mirrors the shelf so search is instant.
> Short-term scratch (checkpoints) stays separate from long-term memory (store).

## 1. What online research forces (delta over v2)

v2 cleaned content but left storage as loose files. Evidence says storage must be a maintained vault + memory-mapped index:

- LangGraph split is strict: **checkpointer** = thread-scoped short-term snapshots (continuity, HITL, time travel, fault tolerance; methods put/put_writes/get_tuple/list/delete_thread; async variants for ainvoke) vs **store** = cross-thread long-term key-value (namespaces tuple like `(user_id, memories)`, put/get/search, prefix search, limit-truncated, insertion-order vs backend-order). This repo already mirrors it (`checkpoint.py` + `store_factory.py`); wiki must map the same way: thread runs stay in checkpointer, curated knowledge lives in store namespaces, never in channel state. Postgres notes: `autocommit=True` + `row_factory=dict_row` required, `setup()` creates tables+indexes once, cron-prune old checkpoints, thread_id<255 chars.
- Vault health is computable without LLM: `vault-maintenance` (orphans, broken `[[links]]`, tag similars like #project vs #projects, frontmatter standardization, content-similarity duplicates, date/topic clustering), `vaultlint` (free instant broken-link job that found 2 dangling + 6 orphans on first run), `Broken Links` plugin (folder/file/link views + heading/block check), `obsidian-headless-cli` (`status|search|links|backlinks|orphans|broken-links|tags|stats|frontmatter`), `VaultQuery` (notes/frontmatter/tags/links/headings/tasks as SQLite, real SQL writes back). `vault-mcp` reference schema is directly reusable: `notes` + `notes_fts` (FTS5) + `links` directed graph + `tags` + optional `notes_vec`, tools `vault_write|vault_write_batch|vault_edit|vault_delete|vault_rename` (auto-rewrite backlinks) + `vault_search` (FTS5/semantic/hybrid) + `vault_backlinks|vault_traverse` + `vault_lint`.
- Search scaling: `qmd` modes `search` (~10ms BM25 FTS5) / `vsearch` (~100ms vector) / `query` (~1-2s hybrid expansion+RRF k=60 + rerank + position-aware blend); `notes-rag` chunk 300/overlap 60 + FTS5 weight 0.4 + vec 0.6 + RRF top-20 + cross-encoder top-6; `fidx` no-LLM-in-path hybrid (18-49ms warm) with `lexical|vector|hybrid` modes + knee/calibrated truncation. Lesson: keep deterministic FTS5 default (zero-cost, offline), add embeddings as opt-in extra only.
- Frontmatter discipline from Memoria/Obsidian docs: malformed YAML hides notes from filtered views; fix raw outside Obsidian, verify via detector + Dataview query. Schema must pin required keys.

## 2. Proposal v3 for this repo

- Canonical page: `knowledge_base/wiki/<topic>/<slug>.md` kebab-case, frontmatter `id,title,type[concept|entity|source|comparison|query],tags,aliases,sources[],updated,tldr_llm,status[active|superseded|archived],confidence[fact|pattern|heuristic]`, body with `## Evidence` + `## Links` + `## Contradictions`. `wiki/_index.md` catalog + `wiki/log.md` chronological, both auto-refreshed, `Rebuild index` sweep available.
- Index: `.wiki.db` SQLite sidecar (derivabile, deletable): `notes|notes_fts|links|tags` per `vault-mcp`; optional `notes_vec` only under new opt-in extra. Watcher incrementally re-indexes on create/modify/delete/rename; rename rewrites backlinks. No new MCP server (AGENTS rule 4); expose via existing ports.
- LangGraph mapping: namespaces `("wiki", <topic>)`, keys = slug, values = frontmatter+summary (not full body); bodies stay in `BodyStoragePort`, full pages on disk. Checkpoints keep `BodyRef`-style pointers only (per 0013), never page markdown. Retention: checkpoint cron-prune + page-history backups `.wiki/history/`.
- Migration: pure file transform `research_store/<thread>/article.md+sources.json` -> wiki pages + lineage (`compiled_from[]`, `contributed_to[]` both directions per `ddsyasas` lineage). Golden-file tests, additive local-first, Supabase buckets as opt-in adapter mirroring `SupabaseArtifactStore`.

## 3. SOLID mapping

Store behind ports only; nodes see `save/load/search/traverse/lint`. `FileWikiStore` vs `SqliteWikiIndex` vs future `SupabaseWikiStore` are drop-in adapters. `engine.py` alone chooses adapter.

## 4. TDD slices

0. Vault-lint contract: broken links, orphans, missing frontmatter, thin articles, index drift — all deterministic, JSON report for CI.
1. Migration golden masters: thread fixtures -> expected wiki pages + index/log diffs.
2. SQLite sidecar: upsert/search/delete + FTS5 ranking + backlink/traverse correctness on fixtures; delete-db rebuild test.
3. Namespace mapping: store put/get/search prefix semantics + limit handling + thread_id length guard.
4. Replay neutrality: eval cassettes bit-identical after v3 (bodies/bytes unchanged, only index added).

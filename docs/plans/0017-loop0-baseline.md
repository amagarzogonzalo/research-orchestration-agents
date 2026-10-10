# 0017 — Loop 0 baseline: repo map, storage audit, duplication inventory

**Status:** Accepted · **Date:** 2026-10-08 · **Type:** investigation, no code
**Related:** 0008 (orchestrator), 0009 (portable library), 0012 (artifact port), 0013 (body refs)
**Sources:** local reads of `engine.py`, `store/`, `orchestration/`, `schemas/`, `harness.toml`, `SPEC.md`, `evals/`, `tests/`, `research_store/`, `knowledge_base/` + online pattern set from Loops 1-4

> For humans: think of this repo as a library that reads papers for you and files
> a report per question under `research_store/<question-id>/article.md`. Today it
> always goes back to the internet, even if you asked almost the same thing last
> week. So reports pile up as loose folders with repeated sources. Loops 1-4 fix
> that by adding a memory (wiki), a cleaner (dedup), and a tidier shelf (vault).

## 1. Engine map (composition root)

`src/knowledge_engine/engine.py` `ResearchEngine` is the only public facade:
`research(query, workflow, thread_id)` + `search` + `scrape` + `browser.act` + `discoverer.select()`.

Construction builds: `ResearchStore(root)` + `FileCheckpointer(<root>/checkpoints)` or postgres via `orchestration/checkpoint.py` + memory store or postgres via `orchestration/store_factory.py` + `DemuxSearchAdapter` + `ScrapeRouter` + provider + discoverer.
Kinds: `checkpointer_kind=file|postgres`, `store_kind=memory|postgres`, `artifact_store_kind=local|supabase`, `body_store_kind` follows artifact kind unless overridden. Postgres requires `await engine.setup()` then `await engine.close()`.

## 2. Orchestration (nodes pure, I/O in infrastructure)

`orchestration/state.py` `WorkflowState`: raw typed channels, `add` reducers for `llm_calls`, `branch_notes`, `branch_sources`, `bodies`, `notes`; last-wins for `status`, `judge_critique`.
`orchestration/graphs.py` `NodeSet` + `build_workflow()`: workflows `research|qa|summary|deep` share node classes, graph-wide retry 3x backoff + 300s run timeout + error compensation to `failed` status.
`orchestration/runtime.py` `GraphRuntime`: thread_id config, interrupt resume via `Command(resume=)`, no external event bus.
Nodes (`orchestration/nodes/`): `IngestNode(search, limit)` writes `sources`; `RelevanceNode(provider)` filters; `BodiesNode` + `FetchBodyNode(scrape, storage)` persist via `BodyStoragePort` and write `BodyRef` only; `SynthesizeNode(provider, storage)` materializes refs; `JudgeNode`, `ReviewNode`, `RenderNode(root, save)`, `IntentNode`, `DecomposeNode`, `BranchNode`, `AggregateNode`, `CriticNode`.
Current path is external-first: every `research()` hits live federated search. No internal wiki lookup exists.

## 3. Storage layout (files-only default)

Per run `research_store/<thread_id>/`: `metadata.json` (query, workflow, status, models), `events.jsonl` (append-only trace, `ts` added on write), `sources.json` (via `ArtifactStorePort.put_sources` + local copy), `article.md` (via `put_article` + local copy, `--- frontmatter --- + body`).
Checkpoints: `<root>/checkpoints/<thread>/<checkpoint>.json` + `.writes.json`, base64 blobs stay UTF-8 JSON.
Bodies: `<root>/bodies/<sha256(url)[:16]>.md` deterministic idempotent, `load()` refuses refs outside root. Sample `knowledge_base/bodies/6d06577100eb9ffb.md` confirms naming.
Artifacts: `LocalArtifactStore` byte-identical default; `SupabaseArtifactStore` buckets `articles|sources` upsert + public URL; `SupabaseBodyStorage` bucket `bodies` same hash naming for cross-machine resume.
Sample `knowledge_base/t1/article.md` is a 17-line fixture with `@id`, `type: KnowledgeBaseArticle`, `provenance`, `citations`, `tldr_llm`.
Observed: `research_store/loop-2/` empty, `loop-4/loop-report.json` single file, `research_store/evals/{bodies,checkpoints,eval-deep,eval-research}/` are replay outputs, not curated wiki pages.

## 4. Duplication inventory (frozen for Loops 1-4)

| Class | Signal in this repo | Detector tier | Keeper policy |
|---|---|---|---|
| Exact copy | same DOI/URL in many `sources.json`; same body hash | SHA256 + DOI/URL key + Bloom | keep first thread, link rest |
| Near copy | same paper via new DOI form, bot-wall HTML vs clean text | SimHash 64-bit Hamming<=3, MinHash 128-256 perm Jaccard>=0.6-0.8 + LSH | union-find cluster, keep longest/cleanest |
| Paraphrase | re-synthesized `article.md` sections across threads | embedding cosine>=0.8 + selective LLM adjudication (<1% pairs) | LLM merge with contradiction flags, close don't delete |
| Containment | parent review vs child paper chunk | suffix-array substring + length ratio check | keep parent, link child |

No measurement of class shares exists yet; Loop 4 eval must report it. That split sets thresholds vs LLM budget.

## 5. Readers (blast radius freeze)

`ResearchStore.run_dir/_resolve/read_*`, `LocalArtifactStore`, `FileCheckpointer`, `LocalBodyStorage`, `evals/snapshots/*` (byte-identical replay assumption), `bin/context_budget.py` (new tools = prompt weight), `tach.toml` layering (schemas leaf, core->schemas, store->core+schemas, infra->core+schemas, orchestration->core+schemas+store).

## 6. Gates

`make test` (`tests/unit/test_research_store.py`, `test_body_storage.py`, checkpointer/store-factory tests, `test_prompt_golden_master.py`), `make evals` offline replay (`_demo`, `research`, `deep`, `biomed`, `web`), `make context|lint|types|arch|semgrep|agents`, full `make check` + `bench-check`.

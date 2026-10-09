# 0022 — Human guide: from piles of reports to a living wiki (loops explained plainly)

**Status:** Accepted · **Date:** 2026-10-08 · **Type:** human overview, no code
**Read this first.** Technical details live in 0017-0021. This file is the same plan in everyday language.

## TL;DR for busy humans

You have a great research robot that always starts from zero. Ask twice about
RAG and hallucinations and you pay twice, store twice, and get two overlapping
reports in `research_store/`.

We fix it in four human ideas:

1. **Keep notes (wiki).** Check your own notes before Googling again.
2. **Clean duplicates.** Same paper filed twice gets merged, not copied.
3. **Tidy shelf (vault).** Every note gets a title card, links, an index and a diary.
4. **Small safe steps.** Build it piece by piece, each with a test and an undo.

No new paid services. Markdown files you already own. Works in Obsidian/VS Code.

## The journey in one picture (in words)

Today:

```text
You ask -> robot searches internet -> reads pages -> writes report folder
You ask again -> robot searches internet again -> reads again -> new folder, same papers
```

After:

```text
You ask -> robot checks wiki notes first
  hit: answer from notes + cite pages, almost free
  miss: search internet once, then file what it learned into notes
Lint (health check) runs weekly: fixes links, flags old claims, finds orphans
```

Concrete example from this repo: `knowledge_base/t1/article.md` is a tiny 17-line
report today with no links. After, it would live at `knowledge_base/wiki/rag-hallucinations.md`
with frontmatter (title, sources, updated, tags), `[[links]]` to `[[retrieval]]` and
`[[evaluation]]`, an entry in `wiki/index.md`, and a line in `wiki/log.md` like
`## [2026-10-08] ingest | RAG hallucinations`.

## Loop by loop, improved after online research

### Loop 0: what we have (the audit)

We read the engine, the workflow nodes, the file store, the tests and evals, and
sampled real folders. Finding: bodies already dedup by URL hash
(`bodies/<sha(url)[:16]>.md`), but articles and sources repeat across threads with
no index. Checkpoints and memory have a cloud path (Supabase/Postgres), published
articles do too via buckets `articles|sources`. Default stays local files so you can
move the folder and keep everything.

Human analogy: the kitchen works, but there is no pantry list. We buy the same
ingredients twice.

### Loop 1: internal-first wiki (Karpathy pattern)

Online research (Karpathy gist, DataCamp explainer, llmwiki.cc, ddsyasas, Astro-Han
SKILL, WiCER paper, Cochran RAG-vs-wiki study) says: don't just cache answers.
Compile knowledge at ingest time. One source updates 10-15 pages. Keep raw
immutable, let the model own the wiki, keep one rulebook (schema). Use `index.md`
as catalog and `log.md` as diary. Works to hundreds of pages with no embeddings;
add `qmd`-style search only when the index saturates. WiCER warns blind copying
fails 53-60% without iterative refine, so every ingest gets New/Update/Disputed/
No-material verdicts with grounding checks.

Improvement over naive cache: contradictions are flagged on the page, good answers
are filed back into `wiki/queries/`, lineage points both ways (page->sources and
source->pages).

### Loop 2: cleaners that go cheap-to-expensive

Online research (text-dedup benchmarks, RETSim, LSHBloom, minhash-dedupe,
NearDuplicateDetection) says one cleaner always lies. So:

- Level 1 free: same DOI/URL/SHA -> drop, link to keeper.
- Level 2 cheap fingerprints: SimHash Hamming<=3 for short texts, MinHash
  128-256 perms Jaccard 0.6 candidate / 0.8 merge + LSH to avoid all-pairs,
  union-find to group A~B~C. Suffix-array for parent-contains-child.
- Level 3 meaning: embeddings cosine 0.8+ on candidates only.
- Level 4 judgment: LLM looks at <1% hard pairs only.

Old pages are closed, never deleted, with `superseded_by` + timestamp. That keeps
history ("what did we believe in March") separate from truth ("what holds now").

Human analogy: sort laundry by obvious colors first, check tricky stains under
light second, ask an expert only for the stained silk shirt.

### Loop 3: nicer shelf (vault + memory done right)

Online research (LangGraph persistence, vault-maintenance, vaultlint, Broken Links,
obsidian-index-service, VaultQuery, vault-mcp, fidx, qmd/notes-rag) says:

- LangGraph rule: checkpointer = short-term scratch per conversation (resume,
  human-approval, time travel). Store = long-term memory across conversations
  in namespaces like `("wiki", topic)`. Never put full pages in checkpoints,
  only pointers (this already saved MBs per run in ADR-0013).
- Vault rule: every page gets a title card (YAML frontmatter), kebab-case name,
  backlinks, orphans/broken-link report as JSON for CI, SQLite mirror
  (`notes|notes_fts|links|tags`, stdlib only, deletable/rebuildable) for instant
  search. FTS5 keyword first (10ms), vectors later only if needed.
- Rename rewrites backlinks. History backups in `.wiki/history/`. Malformed YAML
  hides notes, so lint catches it before humans wonder where a page went.

### Loop 4: safe delivery (the winner)

Five slices, each green on `make check` before the next:

- S0 ports + fakes + contract tests (same test runs on every adapter).
- S1 wiki-first query (hit = zero live calls, miss = identical to today).
- S2 dedup pipeline with golden pairs + report counts.
- S3 migration goldens + delete-and-rebuild index test.
- S4 lint gate + new eval `internal_wiki_demo` (invariants + budget + advisory judge).

We deliberately do NOT: add paid deps, add MCP servers, force Supabase, turn on
embeddings by default, let the agent accept snapshots (human-only), or raise
budgets to pass.

## FAQ in plain words

**Will it still search the internet?** Yes. Wiki first, internet on miss, then file
learnings. Fresh topics cost the same as today, repeats cost almost nothing.

**What if the wiki is wrong?** Every claim links to raw sources. Lint flags
contradictions and stale pages. History is kept, so we can show the succession.

**What if it gets huge?** Index + log carry hundreds of pages. Past that, add the
SQLite FTS5 sidecar (already planned), then optional vectors. Obsidian graph view
itself slows past ~25k notes, so we lint orphans early.

**How do I undo?** Docs phase: delete the branch. Code phase: revert one slice PR,
set engine back to `file|memory|local`, check `git status` is clean.

## What to do next (human checklist)

1. Read this file, then skim 0018-0021 only where curious.
2. Decide: files-only wiki now, Supabase variant later? (keeps 0012 compatible).
3. Approve S0+S1 first (ports + wiki-first query + demo eval). Merging those already
   saves quota on repeats without touching storage.
4. Run `make test` + `make evals` after each slice; accept snapshots by hand only.

Further reading (all fetched during loops): Karpathy gist `442a6bf555914893e9891c11519de94f`,
DataCamp LLM Wiki, llmwiki.cc/docs, Astro-Han SKILL.md, WiCER arXiv:2605.07068,
text-dedup benchmarks, RETSim arXiv:2311.17264, LSHBloom arXiv:2411.04257,
LangGraph persistence/stores docs, vault-mcp/qmd/fidx READMEs, CiteEval/C2-Faith/
FaithLM eval notes.

# What the LLM wiki *is* — a worked example

This answers three questions in order: **what is the point**, **do I need SQL**,
and **how do I store it online**. Grounded in the original pattern
([Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f))
and its canonical implementation ([llmwiki.cc](https://llmwiki.cc/docs)).

## The point, in the source's own words

> "The thing that makes it not-another-RAG-app: **it accumulates something
> readable.** RAG chatbots rediscover your corpus on every query and leave
> nothing behind. Note apps put all the maintenance burden on you. LLM Wiki
> sits between them — the LLM does the maintenance, the wiki accumulates
> value, **you own the markdown files.**"
> — llmwiki.cc

And on storage, plainly:

> "No hidden state, no separate database to migrate, no opaque embeddings
> cache. **SQLite is just a search/index cache — derivable from the markdown
> at any time.** Your wiki is plain markdown files in a folder you fully own."

So the **product is markdown**. Everything else (SQLite, Postgres, vectors) is
an *index over* the markdown — never the source of truth.

## Do I need SQL? No.

You never write SQL. The markdown is what you read, browse, edit, and own. An
index (SQLite locally, Postgres/pgvector online) exists only so the *service*
can search fast — and it is always rebuildable from the markdown. Delete the
index and it regenerates. This is exactly our repo's design:

| | Canonical (yours) | Derived index (rebuildable) |
|---|---|---|
| Local default | `<store_root>/wiki/**/*.md` | `.wiki.db` (FTS5 sidecar) |
| Online option | markdown in Supabase Storage **or** a Postgres text column | Postgres FTS / pgvector |

## A worked example: one small wiki

Topic: **"Does retrieval augmentation reduce hallucinations?"** Two raw
sources were ingested (a survey and a benchmark paper). Here is the *actual
markdown* the wiki holds afterwards. This is the deliverable.

### Layer 1 — raw sources (immutable)

```
research_store/rq-rag-halluc/
  article.md        # the run's synthesized report (our research output)
  sources.json      # the papers it found: arXiv:2412.00001, doi:10.1/bench
  metadata.json
  events.jsonl
bodies/7f3a9c....md # scraped full text (hash-named)
```

### Layer 2 — the wiki (LLM-owned markdown)

`wiki/sources/rag-hallucination-survey.md`
```markdown
---
type: source
title: RAG and Hallucinations — Survey
thread: rq-rag-halluc
confidence: EXTRACTED
sources: [arXiv:2412.00001]
tldr: Grounding generation in retrieved passages lowers unsupported claims.
---
Filed from run `rq-rag-halluc`. Sunmary of the two ingested papers.
```

`wiki/concepts/retrieval-augmented-generation.md`
```markdown
---
type: concept
title: Retrieval-Augmented Generation
confidence: INFERRED
sources: [rag-hallucination-survey]
---
RAG retrieves passages at answer time and conditions the model on them,
grounding output in evidence instead of parametric memory.

## Evidence
- Grounding lowers unsupported claims on open-book QA. [[rag-hallucination-survey]]
- Gains shrink when retrieval returns off-topic passages. [[retrieval-noise]]

## Related
- [[retrieval-noise]]
- [[long-context]]
```

`wiki/comparisons/rag-vs-long-context.md`
```markdown
---
type: comparison
title: RAG vs Long-Context
confidence: INFERRED
sources: [rag-hallucination-survey, long-context-bench]
---
| | RAG | Long-context |
|---|---|---|
| Freshness | reads live sources | frozen at build |
| Cost | retrieval + smaller prompt | large prompt |
| Hallucination | lower on open-book QA | lower on in-corpus recall |
```

`wiki/synthesis/rag-reduces-hallucinations.md`  (a thesis verdict page)
```markdown
---
type: synthesis
title: RAG reduces hallucinations
confidence: INFERRED
---
**Claim:** Retrieval augmentation reduces hallucination.
## Evidence for
- Grounded answers cite retrieved passages. [[rag-hallucination-survey]]
## Evidence against
- Off-topic retrieval can add noise. [[retrieval-noise]]
## Verdict
Supported for open-book QA with a good retriever; weaker for closed corpora.
```

`wiki/queries/what-about-medical-qa.md`  (a filed answer)
```markdown
---
type: query
title: What about medical QA?
confidence: EXTRACTED
---
## Answer
Medical QA benefits most when every claim is footnoted to a guideline. ...
```

`wiki/index.md`  (the catalog the LLM reads first, by category)
```markdown
# Knowledge Base Index

## Sources
- [RAG and Hallucinations — Survey](sources/rag-hallucination-survey.md) — grounding lowers unsupported claims

## Concepts
- [Retrieval-Augmented Generation](concepts/retrieval-augmented-generation.md) — retrieve + condition
- [Retrieval Noise](concepts/retrieval-noise.md) — off-topic passages hurt

## Comparisons
- [RAG vs Long-Context](comparisons/rag-vs-long-context.md)

## Synthesis
- [RAG reduces hallucinations](synthesis/rag-reduces-hallucinations.md) — supported, with caveats

## Queries
- [What about medical QA?](queries/what-about-medical-qa.md)
```

`wiki/log.md`  (append-only diary, grep-parseable)
```markdown
# Wiki Log
## [2026-10-09] ingest | RAG and Hallucinations — Survey
## [2026-10-09] compile | 2 drafts | Does retrieval augmentation reduce hallucinations?
## [2026-10-09] thesis | RAG reduces hallucinations
## [2026-10-09] query | What about medical QA?
```

That's it. A human opens `wiki/` in Obsidian (or the web viewer) and browses
clickable `[[wikilinks]]`, backlinks, and the graph. **No SQL anywhere.**

### Layer 3 — the schema (conventions)

`WIKI_SCHEMA.md` — page types, naming (kebab-case), frontmatter keys, the
ingest/query/lint workflows, and the rule that raw sources are never edited.

## How it maps to this repo

| Example file | Produced by |
|---|---|
| `research_store/<thread>/article.md` | the orchestrator (`render`) — the research output |
| `wiki/sources/<slug>.md` | `wiki_ingest` (files that run's article; `thread` lineage) |
| `wiki/concepts/`, `entities/` | opt-in LLM compile (`wiki_extract`) → reviewed drafts |
| `wiki/synthesis/` | opt-in thesis (`thesis="..."`) |
| `wiki/queries/` | opt-in answer filing (`wiki_save_answers`) |
| `wiki/index.md`, `log.md`, `overview.md` | maintained on every ingest |
| `index.md`-first query, `.wiki.db` FTS5, `WikiGraph` | `wiki_query` / sidecar / graph |

The orchestrator **finds** the research; `wiki_ingest` **ingests** it as
`sources/`; the opt-in compile **distills** it into concepts/entities/thesis;
the index/log keep it navigable.

## How to store it online (the actual question)

The markdown is canonical. Where it *lives* only affects durability and
search:

| Backend | Keeps markdown? | Search built in? | Cost | Verdict |
|---|---|---|---|---|
| **Supabase Storage bucket** (current) | ✅ (as `.md` objects) | ❌ (list + download + score in Python) | free | fine, but not queryable |
| **Postgres text column** (recommended for the service) | ✅ (a `body_md` column) | ✅ Postgres FTS + **pgvector** | free | one DB for LangGraph + wiki; SQL/vectors do the searching for you |
| **Qdrant** | ❌ (embeddings only) | ✅ vectors | extra service | loses the wiki; you'd still keep Supabase |

**Recommendation.** Keep markdown as the artifact. For the online service,
persist the wiki pages as **Postgres rows** (`body_md` text column) in the
same free Supabase Postgres you already use for LangGraph, and let an index do
the searching (Postgres FTS now; pgvector if you want semantic). You never
write SQL — the service does. Qdrant is a downgrade here: it stores vectors,
not a wiki, and adds a second service. `llmwiki.cc` itself keeps markdown as
the truth and treats its database as a *derivable cache* — the same posture as
this repo (`FileWikiStore` + `.wiki.db`).

See also: `docs/data-sources.md`, `docs/mr-before-after.md`, `SPEC.md` (the
online plan), `docs/LLM_WIKI.md`.

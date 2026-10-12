# Before vs After — what is old, what is new

Diagram: [mr-before-after.svg](diagrams/mr-before-after.svg)

This MR did **not** rewrite the research engine. It **wrapped** it in a wiki
layer. Everything on the left below existed and is unchanged; everything on
the right is new in this MR.

## PREVIOUS RESEARCH ENGINE (unchanged — already existed)

| Step | What it is |
|---|---|
| Federated search | OpenAlex, PubMed, Europe PMC, Semantic Scholar, Crossref, arXiv, bioRxiv/medRxiv, HuggingFace Papers, dblp, DOAJ, OSF, Unpaywall, Wikipedia + DuckDuckGo→Tavily→Exa→Serper fallback chain |
| Scrape tier | ar5iv / Europe PMC full text → crawl4ai → trafilatura → Jina; bodies stored as `bodies/<sha256>.md` |
| Relevance → Synthesize | LLM filtering + synthesis over free-tier providers (Groq, Gemini, Mistral, OpenRouter, Ollama) |
| Judge → Review → Render | groundedness loop, human-in-the-loop gate, rendered `article.md` |
| Durable store | `research_store/<thread>/` (article, sources, metadata, events) + `FileCheckpointer` + optional Supabase artifact buckets |

That engine's behavior: **one research run → one folder. It stopped there.**

## NEW IN THIS MR (the LLM wiki layer)

| Piece | What it does |
|---|---|
| `wiki_query` node + `WikiQueryPort` | checks the wiki **before** any live call; exact-query hit → answer with **zero live calls** |
| `clean` node + `SourceCleaner` (`CleanPort`) | dedups the batch (exact-id, MinHash/LSH, containment, close-never-delete) + cross-thread dedup at the wiki layer |
| `wiki_ingest` node + `WikiIngestPort` | files every finished run into the wiki (guaranteed source page, verdicts, SHA cache, index/log/overview) |
| `FileWikiStore` + wiki vault | `sources/ concepts/ entities/ synthesis/ queries/` + `index.md` `log.md` `overview.md` + `WIKI_SCHEMA.md` |
| LLM compile (opt-in) | `RunSettings(wiki_extract, wiki_save_answers, thesis, max_wiki_pages)` → drafts → review (approve/reject/feedback/auto-block) |
| Search & graph | `.wiki.db` FTS5 sidecar + hybrid RRF · `WikiGraph` (nodes/edges/communities) |
| Lint | `FileVaultLinter` (deterministic) + `WikiDeepLinter` (LLM contradictions/stale/gaps) |
| Cloud mirror | `SupabaseWikiStore` + `SupabaseVaultLinter` (`wiki_store_kind="supabase"`) |
| Facade & tooling | `engine.wiki_query()` · `make wiki-lint/-lint-deep/-review/-graph` · `bin/*` helpers |
| Eval | `internal_wiki_demo` active case (recorded, snapshot accepted) + offline hit-path e2e |

New behavior: **one research run → one wiki page, and the next repeat question
is answered from the wiki for free. Research compounds instead of piling up.**

## The one mapping to remember

The old engine **finds** the research. The new wiki layer **ingests** that
research (`sources/`), **compiles** it (concepts/entities/thesis/answers),
**dedups** it, and **serves it back** — all behind ports, all degrade-safe,
all free-tier.

Human overview: [MR-LLM-WIKI.md](MR-LLM-WIKI.md) · plan: [LLM_WIKI.md](LLM_WIKI.md)

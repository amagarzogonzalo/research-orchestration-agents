# 0025 — Source assessment: the 7 wiki projects from your email, verified

**Status:** Accepted · **Date:** 2026-10-09 · **Type:** verification + recommendation
**Method:** fetched every linked repo live 2026-10-09. One correction matters (license), one risk the email missed (maintenance mode), one upgrade (Gemini fit better than stated for SamurAIGPT).

## 1. Verification (all links resolve)

| Project | Exists, scale | Research / discovery | Ingest / compiler | Gemini API fit | Local free fit |
|---|---|---|---|---|---|
| Karpathy gist `442a6bf555914893e9891c11519de94f` | Yes, 5000+ stars/forks, Apr 2026 | Concept only (human-guided) | Concept only | Via your implementation | Via your implementation |
| `nashsu/llm_wiki` | Yes, 20.4k stars / 2.3k forks / 889 commits, **GPL-3.0** | Very good: Deep Research UI, Tavily/SerpAPI/SearXNG, clipper, gap-led topics | Excellent: PDF/Office/EPUB/media/URL, 2-step ingest, SHA256 cache, queue, auto-watch, LanceDB optional, graph Louvain+insights, review queue, MCP+API `:19828`, skill | Excellent: native Google provider docs | Excellent: Ollama + custom OpenAI-compat |
| `nvk/llm-wiki` | Yes, 1.4k stars, MIT, `llm-wiki.net` | Excellent: 5-10 parallel agents, thesis for/against, `--min-time`, collectors, gap pursuit, MediaWiki/wayback/CSV adapters | Very good: raw->wiki compile, audit, checkpoints, outputs | Indirect via runtime (no native Gemini path found) | Partial (Pi/DS4 local lanes, full research needs search+strong model) |
| `atomicstrata/llm-wiki-compiler` | Yes, 2.2k stars, MIT, npm `llm-wiki-compiler` | Limited (URL + Crossref connector, not a web researcher) | Excellent: typed pages, lifecycle profiles, review holds, citation validation, hybrid BM25+embed+graph, lint/eval CI, OKF/JSON/GraphML/Marp export, MCP+SDK | Moderate (Anthropic default; Ollama/OpenAI-compat/Codex/Copilot, no native Gemini emphasis) | Yes (Ollama + local OpenAI-compat) |
| `Astro-Han/karpathy-llm-wiki` | Yes, 2.4k stars, MIT, prod 94 articles/99 sources since Apr 2026 | Limited (URL processing, no discovery program) | Good: ingest->query->lint skill, index/log, citations; explicit non-goals: no vectors/graph/MCP/hooks/OKF | Indirect via host agent | Indirect via host agent |
| `SamurAIGPT/llm-wiki-agent` | Yes, 3.6k stars, MIT, `CLAUDE.md`+`AGENTS.md`+`GEMINI.md` | Limited (gap suggestions, no rigorous discovery) | Good: markitdown PDF/DOCX/PPTX/XLSX/EPUB + arXiv `pdf2md`, entities/concepts/overview, graph Louvain vis.js, lint | **Good-native:** dedicated `GEMINI.md` + Gemini CLI path (better than email's "good through CLI" implies; still agent-mediated, no direct API-key config docs found) | Not local-first (no Ollama-first docs found) |
| `kytmanov/obsidian-llm-wiki-local` | Yes, 833 stars, MIT, **maintenance mode -> Synto successor** | No (user-supplied md only) | Very good local compile: Ollama default (gemma4/qwen), drafts+rejection feedback, hand-edit preservation, query --save/--synthesize, `compare` advisor, watcher | Weak (OpenAI-compat only, no native Gemini) | Excellent (Ollama/LM Studio/vLLM/Groq/Together) |
| `sdyckjq-lab/llm-wiki-skill` | Yes, 2.5k stars, monorepo skill+workbench, CN-first | Moderate (adapters + research actions) | Good: entities/topics/sources/comparisons/synthesis, confidence labels, SHA256 cache, cascade delete, offline HTML graph | Indirect via runtime | Indirect via runtime |

## 2. Corrections to the email table

1. **License:** `nashsu` is **GPL-3.0**, not permissive. Fine to *use* as a desktop tool; do not vendor its code into this MIT repo without legal review. All others above are MIT (verified).
2. **Maintenance risk:** `kytmanov` banner says maintenance mode, new features go to Synto with migration command. Use its *patterns* (draft review, compare advisor), not as a new dependency.
3. **Gemini fit:** SamurAIGPT deserves a bump — first-class `GEMINI.md` + CJK showcase + markitdown pipeline make it the cheapest Gemini-CLI skill path. Still no direct Gemini-API-key config like nashsu's provider screen.
4. **Windows caveat:** atomicstrata changelog states native Windows validation + CI outstanding (#217), filesystem confinement unverified — use Linux for untrusted projects. sdyckjq-lab documents the PS5.1 GB2312/UTF-8 hook garbling fix (`install.ps1`, #16) — relevant to this Win32 PowerShell environment.

## 3. Scores vs your 3 goals (clean anything, Supabase-first, iterative wiki) + repo constraints (zero-cost, Windows, no new MCP/dep without asking)

- Best one-tool answer for Windows + Gemini API: **`nashsu/llm_wiki`. Agree with email.** Only candidate with native Google provider + full ingest + Deep Research + review UI + local Markdown vault. Privacy rule stands: public papers via Gemini free quota; internal/proprietary/confidential via local Ollama path only (Gemini sends text to Google).
- Best research workflow: **`nvk/llm-wiki`. Agree.** Thesis mode + parallel agents + collectors + `--min-time` is the discovery half this repo lacks (our `DemuxSearchAdapter` is federated lookup, not agentic investigation). Cost: broad system, permissions/config overhead; Gemini indirect.
- Best compiler rigor: **`atomicstrata`. Agree.** Review holds, runtime gates, citation validation, eval harness, OKF exchange, lifecycle profiles — the governance layer to copy. Not the discovery answer.
- Best private local compile: `kytmanov` patterns (or Ollama lane of nashsu). Never-Gemini requirement => local models only.
- Cheapest cross-harness skill: Astro-Han (minimal) or SamurAIGPT (multi-format + Gemini CLI). sdyckjq-lab if you want CN docs + graph workbench and accept fast-moving breadth.

Combined score logic (research 45 / compiler 55) is sound because evidence must survive compilation to be reusable; keep it.

## 4. Integration plan for THIS repo (borrow patterns, don't adopt apps)

No new MCP server or pip dep without your explicit approval (AGENTS rule 4). No vendoring GPL code.

- **From nashsu:** two-step ingest (analysis -> generation), `purpose.md` (why) separate from `schema.md` (how), SHA256 incremental cache, persistent queue with retry, source auto-watch on `raw/sources/`, review queue with pre-generated search queries, `overview.md` auto-update, guarantee one source summary even on partial failure. Map to `0024` S1/S3.
- **From nvk:** thesis for/against + `--min-time` rounds + collectors as the missing discovery mode above our federated search; session memory (`.sessions/` digests, feedback candidates, explicit `promote` to `raw/notes/`) as the episodic layer beside wiki facts. Adopt as agent workflow, not code, first.
- **From atomicstrata:** compile `--review` holds, fail-closed invalid config, hash-pinned artifacts, `lint`+`eval` CI gates, OKF-style portable export (our `article.md`+`sources.json` already close). Copy the gate semantics into `make check`.
- **From Astro-Han:** production non-goals worth copying at our scale (no vectors/graph/MCP until grep+read recall measurably degrades; no per-page timers; whole-wiki lint drives maintenance).
- **From SamurAIGPT:** markitdown-style multi-format intake + arXiv `pdf2md` fidelity path + `GEMINI.md` trigger wording for Gemini-CLI runs.
- **From kytmanov:** draft `.drafts/` + rejection feedback injected into next compile + 5-rejection auto-block + hand-edit preservation + `compare` challenger-preview before model switches.
- **From sdyckjq-lab:** `install.ps1` UTF-8 preamble for our Windows scripts; confidence labels EXTRACTED/INFERRED/AMBIGUOUS/UNVERIFIED; cascade delete (source removal cleans derived pages/links/cache).

## 5. Done lines

- Changed: assessment only (`0025` added); no code, no deps, no MCP changes.
- Covered by: live fetches above (7/7 repos resolve; star/commit/license figures quoted from pages).
- Not done: no install of any app, no GPL vendoring, no Gemini runs on private data, no live re-record, no snapshot accept.

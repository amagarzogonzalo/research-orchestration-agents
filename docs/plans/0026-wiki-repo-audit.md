# 0026 — Wiki repo audit: per-repo lessons + adaptation + proposal (skill execution)

**Status:** Proposed · **Date:** 2026-10-09 · **Type:** audit + delivery plan
**Method:** GitHub API facts fetched live 2026-10-09 for all 8 repos + gist page;
web search for `llm-wiki-sample`; READMEs fetched live earlier today. Supersedes
0025 where fresh facts differ (see §4). First execution of `skills/wiki-repo-audit`.

## 1. Verification table (as seen, never from memory)

| # | Project | Stars / forks (API, 2026-10-09) | Default branch | License (API + README) | Last push (API) |
|---|---|---|---|---|---|
| 1 | Karpathy gist `442a6bf…` | 5,000+ stars/forks (gist page) | n/a (single file) | Idea file, no license | Apr 2026 (1 revision) |
| 2 | `llm-wiki-sample` | **UNVERIFIED** — no repo under that identifier found | — | — | — |
| 3 | Astro-Han `karpathy-llm-wiki` | 2,447 / 288 | `main` | MIT / MIT | 2026-07-23 (stale ~2.5 mo) |
| 4 | SamurAIGPT `llm-wiki-agent` | 3,612 / 417 | `main` | MIT / MIT | 2026-10-05 |
| 5 | kytmanov `obsidian-llm-wiki-local` | 833 / 126 | `master` | MIT / MIT | 2026-05-26 (stale ~4.5 mo) |
| 6 | atomicstrata `llm-wiki-compiler` | 2,173 / 234 | `main` | MIT / MIT | **2026-10-09 (today)** |
| 7 | nvk `llm-wiki` | 1,401 / 132 | `master` | MIT / MIT | 2026-10-02 |
| 8 | nashsu `llm_wiki` | 20,351 / 2,298 | `main` | API: NOASSERTION / README: **GPL-3.0** | pushed 2026-09-28 |
| 9 | sdyckjq-lab `llm-wiki-skill` | 2,529 / 297 | `main` | API: **null (no SPDX)** / README claims MIT text present | 2026-07-27 |

`llm-wiki-sample` note: the closest verified neighbor is `tonypalmisano/llm-wiki`
(Markdown-first starter, MIT, adapters for OpenClaw/Claude/Codex/OpenCode,
v0.4.2 draft, demo vault + validation scripts). It is treated as a neighbor
reference in §2, not as the email's item — the email's identifier stays UNVERIFIED.

## 2. Per-repo lessons and adaptations

### 2.1 Karpathy gist (methodology, not software)

**a) Learn.** The canonical contract, quoted from the fetched gist: raw sources
are immutable ("reads from them but never modifies them"); the wiki is a
"structured, interlinked collection of markdown files" the LLM owns entirely
("creates pages, updates them when new sources arrive, maintains
cross-references"); the schema (`CLAUDE.md`/`AGENTS.md`) "makes the LLM a
disciplined wiki maintainer rather than a generic chatbot". Ops: ingest touches
10–15 pages per source; query files valuable answers back ("shouldn't disappear
into chat history"); lint hunts contradictions, stale claims, orphans, missing
pages, gaps. Navigation without embeddings: `index.md` catalog read first,
`log.md` append-only with grep-parseable `## [YYYY-MM-DD] op | Title` prefix
(works to ~100 sources/hundreds of pages). `qmd` hybrid search only at scale.
"Obsidian is the IDE; the LLM is the programmer; the wiki is the codebase."
Applies to any domain (personal, research, book, team, due diligence) — not papers-only.

**b) Adapt.** Adopt the contract verbatim as `knowledge_base/WIKI_SCHEMA.md`
(S1 of 0024 already plans this): raw (`research_store/`, `knowledge_base/bodies/`)
immutable; `knowledge_base/wiki/` LLM-owned with `index.md`/`log.md`/`overview.md`
plus `sources|entities|concepts|comparisons|synthesis/`; ingest verdicts
New/Update/Disputed/No-material. No code to vendor (idea file). Reject nothing.

### 2.2 `llm-wiki-sample` — UNVERIFIED (fetch failed: no such identifier)

**a) Learn.** Nothing verifiable; per hard rules no recalled content is used.
Neighbor signal only: `tonypalmisano/llm-wiki` (found via search) demonstrates the
"starter, not app" niche — shared templates, validation scripts, demo vault,
per-agent adapters, non-blocking review warnings, claim metadata in Markdown tables.

**b) Adapt.** No adoption from the unverified item. Adopt the neighbor's *shape*
if we ever need it: a `core/` workflow + `adapters/` + `examples/demo-vault/` +
`scripts/` consistency-check layout maps 1:1 onto our `core|infrastructure|tests`
split. Flag for user confirmation before creating any starter package.

### 2.3 Astro-Han (2,447★, MIT, stale since Jul 2026)

**a) Learn.** The minimal portable skill: one `SKILL.md` + `references/` +
`scripts/`, installed via `npx add-skill`, works on Claude/Cursor/Codex/OpenCode.
Production proof quoted: "94 wiki articles across 13 topic directories, 99 source
materials ingested, 87 operation log entries in the last 7 days", maintained daily
since Apr 2026. Its design-boundaries section is the real lesson — deliberate
non-goals after 3 months of logs: no source-hash freshness (raw is immutable, so
hashes guard impossible events), no line-number citations (whole-file grep caught
every observed fidelity error), no numeric confidence scores (false precision),
no per-page timers (whole-wiki lint drives maintenance), no vectors/graph/MCP
("at 50K–100K tokens of curated wiki, grep and read are more reliable").

**b) Adapt.** Adopt the non-goals list as our default scope guard for S0–S2
(0021/0024 already lean this way — now with cited justification). Adapt the
triage step (New/Update/Disputed/No-material with log-only on no-material) into
`WikiIngestPort`. Reject embedding search until recall measurably degrades.
Watch item: no push since 2026-07-23 — patterns only, no dependency, no vendoring
of its scripts without re-verification.

### 2.4 SamurAIGPT (3,612★, MIT, active)

**a) Learn.** The multi-format skill: `CLAUDE.md`+`AGENTS.md`+`GEMINI.md` schema
triplet plus Claude slash commands; markitdown auto-conversion (PDF/DOCX/PPTX/
XLSX/HTML/EPUB/IPYNB/audio) with `--no-convert` escape hatch and `pdf2md.py` for
arXiv fidelity; two-pass graph (deterministic `[[wikilink]]` EXTRACTED edges +
LLM-inferred INFERRED/AMBIGUOUS with confidence); Louvain clustering + `graph.html`
self-contained vis.js (no server); CJK showcase; Obsidian symlink-vault pattern
with graph filters excluding `index.md`/`log.md` gravity wells. RAG-vs-wiki table
states the ingest-time contradiction flagging claim explicitly.

**b) Adapt.** Adopt: (i) the `GEMINI.md` schema variant wording as the template for
Gemini-CLI runs of our engine; (ii) markitdown intake shape for our
`ScrapeRouter` (already multi-tier: arXiv/ar5iv → crawl4ai → trafilatura → Jina —
add markitdown-equivalent local conversion as an adapter, opt-in extra);
(iii) EXTRACTED/INFERRED/AMBIGUOUS edge tags for our backlink index. Adapt the
two-pass graph to our `.wiki.db` links table (deterministic pass now, LLM pass
behind review). Reject: vis.js viewer (out of scope), audio ingest (no demand).

### 2.5 kytmanov (833★, MIT, maintenance mode, stale since May 2026)

**a) Learn.** The local-first review machine: Ollama default (fast gemma4 +
heavy qwen), drafts in `wiki/.drafts/`, rejection feedback injected into the next
compile prompt, 5-rejections auto-block with `unblock`, hand-edits detected and
never overwritten, `query --save/--synthesize` with source-hash frontmatter and
duplicate policy (keep_existing/suffix/update_in_place with hash guard),
`compare` challenger-preview in isolated vaults (switch/keep/manual_review verdict),
`watch` file pipeline, ` maint
ain --fix` alias repair + stubs, git `[olw]` commits + `undo`. Banner confirms
maintenance mode → successor Synto with migration command.

**b) Adapt.** Adopt the review loop wholesale into our ingest design: drafts +
feedback-into-prompt + auto-block + hand-edit hash guard + `compare`-before-model-switch
(this directly serves 0024's TDD slices and protects eval replay stability).
Adapt `watch` to our `raw/sources/` auto-ingest trigger. Reject any dependency or
vendoring: maintenance-mode project, patterns only; point future local-model work
at its Synto successor after fresh verification.

### 2.6 atomicstrata (2,173★, MIT, pushed today — most actively maintained)

**a) Learn.** The professional compiler: CLI+SDK+MCP (`llmwiki ingest|compile|
query --save|context|lint|eval|refresh --stale|serve|export`), Configurable
Lifecycle Profiles (fail-closed `.llmwiki/profile.json` declaring entities,
relations, gates, workflows — enforced by runtime, not prompts), review holds
(`compile --review`), citation validation with line ranges, hybrid retrieval
(semantic chunks → BM25 rerank → graph expansion), `eval` (health score,
per-page distribution, citation coverage/precision, regression deltas, cached
judge verdicts), OKF/JSON/GraphML/Marp/`llms.txt` exchange with review-first
import, AutoSci/Newsroom templates, parallel compile with concurrency cap,
content-hash-aware embedding updates. Caveats from its own changelog: Windows
validation/CI outstanding (#217), untrusted projects belong on Linux.

**b) Adapt.** Adopt the governance semantics as our S3/S4: review holds,
fail-closed invalid config (abort compile, never silently disable review),
hash-pinned artifacts, `lint`+`eval` CI gates, staged import of external bundles.
Adapt CLP as a validated `profile.json`-style contract for our wiki schema
(our `tach check` already enforces the layering CLP-style). Reject: MCP server
and SDK surface (AGENTS rule 4 — flag for approval), Marp/GraphML exports (no
demand), Windows-unverified confinement paths (note #217 in rollout docs).

### 2.7 nvk (1,401★, MIT, default branch `master`, active)

**a) Learn.** The research operating system: 5–10 parallel agents
(academic/technical/applied/news/contrarian, `--deep`, `--retardmax`),
thesis for/against with verdict, `--min-time` rounds, collectors with
scale/media/inventory controls, datasets manifests (data stays external),
session memory (`.sessions/` digests + feedback candidates + explicit `promote`
to `raw/notes/`), checkpoints (`docs/knowledge/<slug>/` sealed handoffs),
`--local` project wikis, generated Codex/OpenCode/Pi packaging from one Claude
source of truth with sync-drift tests. Query depths (quick/standard/deep,
`--resume`) and fuzzy router complete the loop.

**b) Adapt.** Adopt as *agent workflow first, code second*: thesis mode +
`--min-time` rounds sit above our `DemuxSearchAdapter` (federated lookup, not
agentic investigation — the discovery half we lack); session digests +
explicit promote map onto our checkpointer (thread scratch) vs store
(cross-thread memory) split. Adapt collectors' scale controls
(tiny→huge write behavior) for our ingest fan-out bounds (protects eval call
budgets). Reject: broad plugin install, sandbox permission surface, remote-write
adapters (explicit non-goals until approved).

### 2.8 nashsu (20,351★, README GPL-3.0 / API NOASSERTION, active)

**a) Learn.** The all-in-one desktop (Tauri+Rust+React): two-step ingest
(analysis → generation) with SHA256 incremental cache, persistent crash-safe
queue, folder import with path-as-classification-hint, source auto-watch with
delete cascade; `purpose.md` (why) separate from `schema.md` (how); 4-signal
graph relevance (direct ×3, source-overlap ×4, Adamic-Adar ×1.5, type affinity)
+ Louvain + insights (surprising connections, isolated/sparse/bridge gaps with
one-click Deep Research); retrieval pipeline (tokenized + optional LanceDB
vector + graph expansion + 60/20/5/15 budget split; recall 58.2%→71.4% with
vectors); async review queue with pre-generated search queries; Chrome clipper;
local API `:19828` + MCP + one-command skill install; multi-provider incl.
native Google + Ollama. License split noted: README declares GPL-3.0 while the
API reports NOASSERTION — README governs.

**b) Adapt.** Use as a tool, never vendor (GPL-3.0 vs this repo's MIT).
Adopt patterns into our slices: two-step ingest, `purpose.md`, SHA cache,
crash-safe queue semantics, review queue with pre-generated queries,
`overview.md` auto-update, 4-signal relevance for our links table, budget-split
prompt assembly. Reject: desktop vendoring, LanceDB default (opt-in extra only),
MCP/API coupling (flag for approval). Privacy rule stands: public sources via
Gemini quota; confidential via local Ollama only.

### 2.9 sdyckjq-lab (2,529★, license null — no SPDX detected, pushed Jul 2026)

**a) Learn.** The cross-platform monorepo: one skill + `workbench/` dev app
sharing a graph engine (offline HTML + app view from one prepare step);
CN-first docs; `install.ps1` UTF-8 preamble fixing PS5.1 GB2312 hook garbling
(#16 — directly relevant to this Win32 env); confidence labels
EXTRACTED/INFERRED/AMBIGUOUS/UNVERIFIED; SHA256 dedup cache + write-through
updates; dialogue crystallization (`sessions/` → pages); SessionStart auto-inject;
safe rename with full impact preview; degraded-state guarantees (clear stale,
never leave operable half-products).

**b) Adapt.** Adopt: `install.ps1`-style UTF-8 preamble for our Windows scripts;
confidence labels for wiki claims; conversation-crystallization trigger
(valuable answers → `wiki/queries/`); safe-rename-with-preview for slug changes.
License caution: API reports no license — treat code as all-rights-reserved,
ideas only, no copying until SPDX confirmed. Reject workbench adoption
(fast-moving, dev-stage).

## 3. Summary: winners per goal (+ constraints)

| Goal | Winner | Runner-up | Why (fresh evidence) |
|---|---|---|---|
| (1) Clean useless data, any type | **atomicstrata** gating + **kytmanov** review loop (joint) | SamurAIGPT intake | Only atomicstrata enforces quality at the write path (fail-closed gates, hash-pinned artifacts, staged imports); only kytmanov proves human-in-the-loop correction compounds (feedback-into-prompt, auto-block, hand-edit guard). Both are dependency-free patterns. |
| (2) Supabase-first lookup on new research | **nvk** session/checkpoint model adapted to our stores | nashsu auto-watch | nvk is the only design separating scratch (sessions) from distilled notes (explicit promote) — exactly our checkpointer-vs-store split plus Supabase Storage buckets. nashsu's auto-watch covers the local trigger half. |
| (3) Iterative Karpathy wiki | **Karpathy contract + Astro-Han scope guards**, tooling from **nashsu** | atomicstrata profiles | Contract fidelity (Astro-Han proves minimal works in production: 94 articles/99 sources) + nashsu's queue/purpose/review machinery, minus its GPL code. |
| Zero-cost / Windows / privacy constraints | **Astro-Han + kytmanov lanes** | sdyckjq-lab install fix | No-embedding default (Astro-Han), Ollama-first local path (kytmanov), PS5.1 UTF-8 preamble (sdyckjq-lab #16). Confidential data never touches Gemini. |

Disagreements with 0025: (i) SamurAIGPT upgraded — `GEMINI.md` + markitdown + CJK make it the cheapest Gemini-CLI *skill* path, though nashsu keeps the one-*tool* crown via native provider UI; (ii) Astro-Han velocity flag — no push since Jul 2026, downgraded from "adopt" to "patterns with re-verification"; (iii) sdyckjq-lab license null — new caution, ideas only; (iv) nvk/nashsu branch/API details corrected above; (v) `llm-wiki-sample` stays UNVERIFIED — 0025 never verified it either, now explicit.

## 4. Proposal: vertical slices (adopt 0024, amend per audit)

- **S0 ports+fakes+contracts** (unchanged): `CleanPort`, `WikiQueryPort`, `WikiIngestPort`, `VaultLintPort` in `core/ports.py`; fakes in `tests/conftest.py`; contract suites RED first. Add Astro-Han non-goals as scope comments in the port docstrings.
- **S1 cleaner** (amended): golden pairs + `minhash-dedupe`-style report; add atomicstrata fail-closed rule (invalid cleaner config aborts, never silently disables) + kytmanov hand-edit hash guard.
- **S2 Supabase-first query** (amended): hit short-circuits live-search spy; add nvk session-vs-promote semantics (checkpointer scratch vs store distilled) + `LESSON:` trigger rule (check wiki on recognizable tool-failure signature after first hypothesis fails).
- **S3 wiki iterate** (amended): migration goldens + grounding invariant; add nashsu two-step ingest + `purpose.md` + SHA cache + review queue with pre-generated queries; SamurAIGPT edge tags on the links table; sdyckjq-lab confidence labels.
- **S4 lint+eval** (amended): deterministic lint JSON + retrieval-counter gaps + prompt-chars-reaching-model log; extend `evals/cases/internal_wiki_demo.yaml` with wiki-hit path + lineage + index/log invariants; judge stays advisory.
- **Non-goals** (no approval sought): new pip deps, MCP server, Supabase cutover beyond adapter shape, vendoring any GPL code, snapshot accept by agent, Marp/GraphML/vis.js viewer, audio ingest, remote-write adapters.

## 5. Done lines

- Changed: audit report only (`0026` added); no source, store, or eval files touched.
- Covered by: 8/8 live API verifications + gist page + README fetches today + 1 recorded failure (`llm-wiki-sample` UNVERIFIED) + `make context` green (next step).
- Not done: no code, no installs, no private-data runs, no live re-record, no snapshot accept, no license legal review (flagged where needed).

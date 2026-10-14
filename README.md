# Knowledge Engine

[![CI](https://github.com/amagarzogonzalo/research-orchestration-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/amagarzogonzalo/research-orchestration-agents/actions/workflows/ci.yml)
[![CI-full](https://github.com/amagarzogonzalo/research-orchestration-agents/actions/workflows/ci-full.yml/badge.svg)](https://github.com/amagarzogonzalo/research-orchestration-agents/actions/workflows/ci-full.yml)

Autonomous, **zero-cost** single-user research-curation library: a Hexagonal core
orchestrated by LangGraph with files-only persistence under `research_store/`.
Built strictly on free-tier LLMs (Gemini Flash / Groq LPU / OpenRouter / Mistral)
with OpenTelemetry observability — no paid LLM tiers, no paid SaaS monitoring,
no broker, no database server.

Design rationale lives in [docs/plans](docs/plans/0000-overview.md) as ADRs.

**New here?** Start with [docs/quickstart.md](docs/quickstart.md) — a no-keys
offline path in ~2 minutes, plus the deployed service at
<https://research-orchestration-agents.onrender.com/docs>.

## Layout

```
src/knowledge_engine/
  core/            Hexagonal ports, exceptions, local pricing table
  schemas/         Pydantic domain contracts (the cross-port API)
  infrastructure/  adapters: rate-limit, circuit, router, search, scrape, providers
  orchestration/   LangGraph graphs + SOLID node classes (ADR 0008)
  observability/   OpenTelemetry GenAI (ADR 0006)
  store/           files-only persistence: ResearchStore + FileCheckpointer
  engine.py        public facade (ResearchEngine)
```

The runtime is a **single-process LangGraph workflow**: nodes read/write typed
state, durability comes from `FileCheckpointer`, artifacts land as plain files.
See `docs/plans/0008-langgraph-orchestrator.md` (0007's event shell was removed
for the single-user design).

## Install

Prerequisite: Python **3.11 or 3.12** and [uv](https://docs.astral.sh/uv/).

```bash
make install            # uv sync --extra dev
cp .env.example .env    # fill free-tier LLM keys (optional for offline use)
```

Runtime floor: **Python 3.11 or 3.12** (`requires-python` enforces this).
See [docs/quickstart.md](docs/quickstart.md) for the offline and online paths.

## Wiki layer (Karpathy pattern, files-first)

Every workflow (`research`/`qa`/`summary`/`deep`) is **wiki-first**: before
any live federated call the compiled wiki at `<store_root>/wiki` is consulted;
an exact-query hit answers from the wiki with zero live search/scrape/LLM
calls, and the finished run is compiled back into the wiki (guaranteed source
page, cross-thread content dedup, index/log/overview). In short: **the
research the agent finds is the content the wiki ingests** — raw runs stay
immutable under `research_store/<thread>/` + `bodies/`, and the wiki files,
dedups, and (opt-in) compiles that research. Opt-in per run via
`RunSettings(wiki_extract=True, wiki_save_answers=True, thesis="...")` for the
LLM concept/entity compile, filed answers and thesis verdict pages.

Tooling: `make wiki-lint` (health gate), `make wiki-lint-deep` (opt-in LLM
contradictions/stale/gaps), `make wiki-review` (draft loop), `make wiki-graph`
(knowledge graph JSON), `bin/wiki_supabase_provision.py` (cloud mirror).
Ask the wiki directly (no live call): `await engine.wiki_query("topic")`.
Full delivery report: `docs/plans/0027-0030`, plan: `docs/LLM_WIKI.md`.

## Cloud service (Render + Supabase)

Live: <https://research-orchestration-agents.onrender.com> (Swagger at `/docs`).

The library also ships a zero-cost HTTP facade (`src/knowledge_engine/service/`,
issue #30) so the research + wiki run fully online:

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | none | liveness (platform probe) |
| GET | `/readiness` | none | DB + Storage checks |
| POST | `/research` | bearer | 202 → poll `/result/{id}`; `workflow` = research/qa/summary/deep |
| GET | `/result/{task_id}` | none | durable job status (Postgres `service_jobs`) |
| GET | `/wiki/query?q=&limit=` | bearer | answer from the compiled wiki, zero live calls |
| POST | `/wiki/ingest` | bearer | re-ingest a raw run |
| POST | `/wiki/lint` | bearer | deterministic vault lint |

Config is env-only — the cutover switches (`CHECKPOINTER_KIND`, `STORE_KIND`,
`ARTIFACT_STORE_KIND`, `BODY_STORE_KIND`, `WIKI_STORE_KIND`) select the Supabase
cloud stack; `API_TOKEN` gates data routes; `RATE_LIMIT_PER_MINUTE` throttles per
key. Storage buckets are **private** (reads go through the API). Deploy steps and
the full env contract live in [`docs/deploy-render.md`](docs/deploy-render.md).

Runbook: `make retention` prunes artifacts older than 30 days (keeping the newest
5 runs) and old `service_jobs`, warning above 80% of the free limits; a weekly
workflow runs it. `.github/workflows/keepalive.yml` pings daily (anti-pause) and
files one `qa` run so the wiki keeps growing. Set `API_TOKEN_READONLY` for safe
read-only sharing (`/wiki/query` + `/result` without write access).

## Run

```python
import asyncio
from knowledge_engine.engine import ResearchEngine


async def main() -> None:
    engine = ResearchEngine()
    result = await engine.research("state of the art in multi-agent orchestration")
    print(result.state.output_path)


asyncio.run(main())
```

## Test

```bash
pytest
```

The rate limiter + fallback router are unit-tested against fakes (no network, no Redis),
so the resilience core is verifiable offline.

## Evals

Behavioral regression over recorded cassettes: `make evals` replays offline
(free, deterministic); `make evals-live` re-records against live APIs (costs
quota — ask first). Full operator manual (record/replay/accept loop,
budgets, troubleshooting): [`evals/README.md`](evals/README.md).

Where results land: replayed artifacts under `research_store/evals/`
(`eval-research/article.md`, `eval-deep/article.md`), baselines in
`evals/snapshots/`, raw evidence (every search/scrape/LLM call) in
`evals/cassettes/<case>/run0/`.

## Troubleshooting

- **Crawl4AI fails with missing browser executable:** the installed browsers
  must match the playwright build — run `python -m playwright install chromium`
  after any playwright upgrade.
- **Evals suddenly re-record or hit the network:** `EVAL_MODE=record` leaked
  into the environment forces record mode. Unset it; the runner takes
  `--record` explicitly, and refuses dirty cassette dirs without `--force`.
- **Free-tier 429s / judge 404s:** shared pools throttle and free slugs get
  retired (e.g. the `:free` suffix scheme). Back off and retry; if a model is
  gone, re-pin `judge_model` in `harness.toml` and probe-verify the new pin.

## Zero-cost discipline

- OpenAlex: single DOI/ID lookups are **free & un-metered** — prefer them over search.
- Semantic Scholar: shared 1k req/s public limit — always behind the token bucket + circuit breaker.
- OpenRouter: ~20 RPM, no SLA — reserved for the fallback cascade only.
- Cost is recomputed locally (never trusted from vendor) for internally-consistent metrics.

## Federated search sources

All keyless. The set mirrors industry practice (Elicit: Semantic Scholar +
PubMed + OpenAlex; ScholarXIV/Academix add arXiv, Crossref, dblp) with an
OA-full-text layer on top:

| Source | Role | Auth / limits |
|---|---|---|
| OpenAlex | DOI-first metadata backbone (free per-ID lookups) | mailto polite pool |
| PubMed (E-utilities) | Biomedical depth, 33M+ records, MeSH syntax | keyless, self-throttled 3/s |
| Europe PMC | Biomedical + OA full-text links | keyless |
| Semantic Scholar | Influence/citation signal | keyless (shared quota) |
| Crossref | DOI registry fallback (fetch + validation) | mailto polite pool |
| arXiv | Physics/math/CS preprints | keyless |
| bioRxiv / medRxiv | Life-science preprints: freshest ~14d slice + DOI fetch (archive via OpenAlex) | keyless |
| HuggingFace Papers | Community-ranked ML papers, code links | keyless |
| dblp | CS bibliography, venue metadata | keyless, bot-gated from some networks (degrades gracefully) |
| DOAJ | OA journals, full-text links | keyless |
| OSF Preprints | Preprint discovery across disciplines | keyless |
| Unpaywall | Legal OA PDF resolution by DOI (fetch only) | real `UNPAYWALL_EMAIL` required |
| Wikipedia | General-knowledge fallback (MediaWiki API) | keyless, always on |
| DuckDuckGo | General-web chain, first provider (HTML results, no signup) | keyless, always on |
| Tavily | General-web chain, agent-search fallback | `TAVILY_API_KEY`; 1,000 free credits/month, no card; skipped when absent |
| Exa | General-web chain, neural/semantic fallback | `EXA_API_KEY`; $10/month free credit, no card; skipped when absent |
| Serper | General-web chain, independent Google-grade fallback (disjoint vendor family) | `SERPER_API_KEY`; 2,500 free searches/month, no card; skipped when absent |

The general-web tier is a single sequential fallback chain
(DuckDuckGo → Tavily → Exa → Serper) wrapped in a disk cache, so a query spends at most
one provider's quota and repeat queries spend none. Each attempt emits an
observability span + counter (`outcome`: success/empty/failure/quota/breaker) so
quality gaps localize instantly. Each keyed provider keeps a
persisted monthly ledger; an exhausted or rate-limited provider is skipped
until it refills rather than retried.

Scrape tier: arXiv/ar5iv + Europe PMC fullTextXML first (source-specific full
text), then crawl4ai → trafilatura (local, free) → Jina Reader (remote last
resort, keyless ~20 RPM, optional `JINA_API_KEY`). Papers With Code is
officially sunset; its successor (HuggingFace Papers) is wired above.

Deliberately not wired: ORCID (researcher profiles, not papers), OpenCitations
(no verifiable live endpoint: v1 410, v2 paths 404/400), OpenAIRE (OAF schema
too nested to map robustly; same records arrive via OpenAlex), key-gated APIs
(CORE, BASE, Lens, ADS), Google Scholar (no API; scraping violates ToS).

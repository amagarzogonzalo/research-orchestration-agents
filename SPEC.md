# Knowledge Engine — Supabase + Render Free Deployment (research + LLM wiki, fully online)

**Status:** v0.2 · **Author:** repo audit + LLM-wiki delivery (plans 0017–0030)
**Repos / refs:** `research-orchestration-agents` · migrations verified live (issues #23/#24, plans 0029–0030)
**Target:** zero-cost production on Render Free + Supabase Free
**Constraint:** light usage (1–3 runs/day), `make check` green at every step

## 0. Status matrix (what already ships vs what this doc moves forward)

| Phase | Area | Status |
|---|---|---|
| 0 | Supabase connectivity + buckets | **SHIPPED** (`SupabaseArtifactStore`, `SupabaseBodyStorage`, `SupabaseWikiStore` create buckets on demand; live-verified) |
| 1 | Artifacts → Supabase Storage | **SHIPPED** (`article.md`/`sources.json`; buckets `articles`, `sources`) |
| 2 | Postgres checkpointer + cross-thread store | **SHIPPED** (`checkpoint.py`, `store_factory.py`, `engine.setup()` creates tables) |
| 2.5 | **LLM wiki cloud persistence** | **SHIPPED** (`SupabaseWikiStore` bucket `wiki`; `wiki_store_kind="supabase"`; cloud cache coherence; `SupabaseVaultLinter`) |
| 3 | Render web service (HTTP research + wiki API) | **OPEN** — the rest of this doc |
| 4 | CI/CD deploy pipeline to Render | **OPEN** |
| 5 | Observability + runbook | **OPEN** |

## 1. Environment contract

`YAML`-driven, `engine.py` reads kinds + env directly. `make check` guards the
code; Render env vars mirror `.env.example` (1:1). Required vars:

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | postgres checkpointer + store | Supabase session pooler `:5432?sslmode=require` |
| `SUPABASE_URL` / `SUPABASE_SECRET_KEY` | artifact/body/wiki Storage adapters | service_role; buckets created on demand |
| `GROQ/GEMINI/MISTRAL/OPENROUTER_API_KEY` | provider fallback router | free tiers only |
| `OPENALEX_MAILTO` / `CROSSREF_MAILTO` | scholarly polite pools | real mailbox |
| `JINA_API_KEY` (opt), `TAVILY/EXA/SERPER` (opt) | scrape/web fallback tiers | skipped when absent |
| `UNPAYWALL_EMAIL` | Unpaywall OA lookup | real address, else skip |
| `API_TOKEN` **(new, optional)** | HTTP facade auth | protects `/research`, `/wiki/*` on non-localhost |

Cutover switches (all env-driven; code defaults stay `file/memory/local`):
`CHECKPOINTER_KIND=postgres`, `STORE_KIND=postgres`,
`ARTIFACT_STORE_KIND=supabase`, `BODY_STORE_KIND=supabase`,
`WIKI_STORE_KIND=supabase`. Rollback = unset the switches; local files remain
the cold backup.

## 2. Cloud persistence mapping (including the wiki)

| Layer | Local (default, portable) | Cloud (Render prod) |
|---|---|---|
| Raw research | `research_store/<thread>/` + `bodies/<sha256>.md` | Supabase Storage `articles/`, `sources/`, `bodies/` |
| Checkpoints | `FileCheckpointer` (JSON) | `AsyncPostgresSaver` (`checkpoints` tables) |
| Cross-thread memory | `InMemoryStore` | `AsyncPostgresStore` (`store`) |
| **LLM wiki** | `<store_root>/wiki/**` (markdown) | `SupabaseWikiStore` bucket `wiki` (pages, drafts, index/log, `.cache`) |
| Wiki lint | `FileVaultLinter` | `SupabaseVaultLinter` |

On Render Free the FS is ephemeral, so **everything the service writes must be
cloud** — the wiki is fully cloud-backed via `WIKI_STORE_KIND=supabase`
(verified live: ingest → search → dedup round-trip against the real bucket).

## 3. Render Free facts that shape the design

- **750 hours/month**, **512MB RAM**, **15-min idle sleep** (cold start ~1 min).
- **No free cron jobs** on Render → the scheduler is a **GitHub Actions
  scheduled workflow** that `POST /research` to the service (zero-cost wake +
  run). `research-live.yml` already exists and can be adapted.
- **Browser automation is out** (Chromium ≈ OOM on 512MB). Keep the `scrape`
  tier in (ar5iv/EuropePMC/trafilatura/Jina) without the playwright extra if
  VM pressure binds — measure first (ADR: `WEB_CONCURRENCY=1`).
- Supabase Free: 500MB Postgres + 1GB Storage; pauses after 7d idle — a
  scheduled ping prevents it.

## 4. Phase 3 — HTTP service (the "fully online" core) — OPEN

Deliverables in the repo: `api.py` (FastAPI), `Dockerfile`, `render.yaml`,
`.dockerignore`, `bin/api_run.py` (or entrypoint). **Dependency gate
(AGENTS rule 4):** FastAPI + Uvicorn are NOT in `pyproject.toml` — this phase
requires explicit approval to add them (pin `fastapi>=0.115,<1`,
`uvicorn[standard]>=0.30,<1`, and drop browser extra from prod image).

API surface:

```
GET  /health            -> {"status":"ok","engine":"ready"}  (no auth)
GET  /readiness         -> checks Supabase buckets + DB (no auth)
POST /research          {query, workflow?, thread_id?, settings?}  -> 202 {task_id}
GET  /result/{task_id}  -> {status, result, error}   (poll)
GET  /wiki/query        ?q=&limit=   -> wiki pages (no live call; Karpathy Query op)
POST /wiki/ingest       {thread_id}  -> re-ingest a raw run into the wiki
POST /wiki/lint         -> deterministic + optional LLM deep lint report
```

Engine composition (single object, app lifetime):

```python
engine = ResearchEngine(
    checkpointer_kind="postgres",
    store_kind="postgres",
    artifact_store_kind="supabase",
    body_store_kind="supabase",
    wiki_store_kind="supabase",
    database_url=os.environ["DATABASE_URL"],
    auto_approve_review=False,          # HITL via /review endpoints if enabled
)
await engine.setup()                    # creates checkpoints/store tables
```

Notes:
- **Jobs are in a Postgres-backed table** (small `jobs` table via the store),
  not in-memory: Render may recycle the process; the poll endpoint reads
  durable status. Worker = an in-process asyncio loop consuming queued rows
  (single worker, sequential — matches 1–3 runs/day).
- `service_role` (`SUPABASE_SECRET_KEY`) never leaves the server; if the wiki
  must be public, expose a read-only endpoint that proxies, never the bucket
  as public.
- Bearer token on mutating routes when `API_TOKEN` is set.

`Dockerfile` (multi-stage, no dev deps, `--extra store,providers`):

```
FROM python:3.12-slim AS base
+ uv sync --frozen --no-dev --extra store,providers   # + fastapi,uvicorn after approval
CMD uvicorn api:app --host 0.0.0.0 --port 10000 --workers 1
```

`render.yaml`: web service, `plan: free`, `dockerfilePath: ./Dockerfile`,
`healthCheckPath: /health`, `EVAL` none, env vars = §1 list (secrets marked
`sync: false`).

Acceptance: `curl POST /research` → `202` → poll `GET /result/{id}` →
`completed` **and** `GET /wiki/query` returns the filed page; repeat query
returns **zero live calls**; restart keeps state (Postgres + Storage).

## 5. Phase 4 — CI/CD pipeline — OPEN

- `.github/workflows/deploy.yml` (push to `main` + `workflow_dispatch`):
  gate `make check`, then `render/deploy-action@v1` with
  `RENDER_SERVICE_ID` + `RENDER_API_KEY` secrets.
- **Scheduler** `.github/workflows/research-live.yml`: cron on a schedule
  (`cron: '0 7 * * *'`) → `curl POST $RENDER_URL/research` with the day's
  query + `wiki_save_answers: true`; this wakes the service and grows the
  wiki zero-cost.
- Branch protection: `make check` must pass; no direct push to `main`.

## 6. Phase 5 — Observability + runbook — OPEN

- `/health` + `/readiness`; structured JSON logs (stdout; Render Logs tab).
- OpenTelemetry available for the free OTLP collector if used; default no-op.
- Runbook in-repo: cold-start 503 (retry 60s), Supabase pause (scheduled ping),
  OOM (single worker, no browser), DB connection errors (pooler DSN), and the
  7d Supabase pause → prevention.

## 7. Acceptance criteria (Definition of Done)

| # | Gate |
|---|---|
| 1 | Dependency add (fastapi/uvicorn) approved + pinned; `make check` green |
| 2 | `api.py` + Dockerfile + render.yaml merged; local smoke: `uvicorn api:app` → `/health` ok |
| 3 | Deployed on Render Free; `POST /research` → completed against real Supabase (Postgres + Storage + wiki bucket) |
| 4 | `GET /wiki/query` returns filed page; repeat query spends zero live calls (verified from service logs) |
| 5 | Scheduled workflow runs daily and the wiki grows; `/readiness` green after idle wake |
| 6 | Runbook covers the top 5 failure modes; rollback = unset switches / disable workflow |

## 8. Rollback

| Phase | Rollback |
|---|---|
| 3 | Delete the Render service; run locally (`file/memory/local`) |
| 4 | Disable `deploy.yml` + scheduler workflow |
| 5 | Delete runbook entries |

## 9. Next immediate actions

1. Approve the FastAPI/Uvicorn dependency add (only out-of-tree dep for Phase 3).
2. Branch `feat/render-service` → `api.py`, `Dockerfile`, `render.yaml`, jobs table, smoke test.
3. Open the Render project (pause-immune ping), set env vars from §1.
4. Enable `deploy.yml` + daily scheduler. See issue (workstream) opened from this spec.

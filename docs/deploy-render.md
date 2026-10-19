# Deploying the HTTP service on Render Free (issue #30)

Target: Render **Free** web service + Supabase **Free** (Postgres + Storage).
Zero-cost, single worker, no browser. The Dockerfile installs only
`--extra store,providers` (no Chromium: 512 MB cannot host it).

## Environment variables to set on Render

Secrets are marked `sync: false` in `render.yaml` and set in the dashboard.
Copy the values from your local `.env` (never commit them).

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes (secret) | Supabase **session-pooler** DSN (IPv4), `postgresql://postgres.<ref>:...@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` |
| `SUPABASE_URL` | yes (secret) | Supabase project URL |
| `SUPABASE_SECRET_KEY` | yes (secret) | service_role key (server-side only) |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY` | yes (secret) | free-tier LLM fallback router |
| `OPENALEX_MAILTO`, `CROSSREF_MAILTO` | yes (secret) | scholarly polite pools (real mailbox) |
| `UNPAYWALL_EMAIL` | optional | OA PDF lookup; blank = skip |
| `JINA_API_KEY` | optional | raises the Jina scrape tier |
| `TAVILY_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY` | optional | general-web fallback chain |
| `API_TOKEN` | yes (secret) | write token for every data route; unset + `ALLOW_ANONYMOUS=false` → 503 and `auth=false` on `/readiness` |
| `API_TOKEN_READONLY` | optional (secret) | read-only token for `/wiki/query` + `/result` (safe sharing) |
| `ALLOW_ANONYMOUS` | optional | local development only; never `true` in production |
| `RATE_LIMIT_PER_MINUTE` | optional | per-hashed-caller limit on data routes (default 30; `<= 0` disables). Shared Postgres limiter when `DATABASE_URL` is set |
| `JOB_TIMEOUT_SECONDS` | optional | abort a run after this long (default 900; `<= 0` disables) |
| `MAX_CONCURRENT_JOBS` | set by blueprint | jobs executed at once (default 1; Render Free RAM) |
| `MAX_QUEUED_JOBS` | set by blueprint | queue depth cap; over → 429 (default 16; `<= 0` disables) |
| `MAX_ACTIVE_JOBS_PER_CALLER` | set by blueprint | per-caller queued+running cap; over → 429 (default 4; `<= 0` disables) |
| `JOB_STALE_SECONDS` | set by blueprint | a running claim older than this is requeued (default timeout + 60) |
| `JOB_MAX_ATTEMPTS` | set by blueprint | attempts after which a recovered job fails instead of retrying (default 3) |
| `JOB_POLL_MS` | set by blueprint | worker idle poll interval (default 500 ms) |
| `AUTO_MIGRATE` | set by blueprint | apply pending `supabase/migrations` at boot (default true) |
| `CLEANUP_AFTER_RUN` | optional | delete a thread's checkpoints after each run (default true; protects 500 MB) |
| `ENABLE_DOCS` | set by blueprint | serve Swagger/OpenAPI (prod `false`; staging `true`) |
| `CHECKPOINTER_KIND` | set by blueprint | `postgres` |
| `STORE_KIND` | set by blueprint | `postgres` |
| `ARTIFACT_STORE_KIND` | set by blueprint | `supabase` |
| `BODY_STORE_KIND` | set by blueprint | `supabase` |
| `WIKI_STORE_KIND` | set by blueprint | `supabase` (or `postgres` for the relational wiki) |
| `WEB_CONCURRENCY` | set by blueprint | `1` (512 MB desk math) |
| `LANGGRAPH_STRICT_MSGPACK` | set by blueprint | `true` (serde allow-list exists) |

`PORT` is provided by Render; the CMD binds it (`${PORT:-10000}`).

## Networking (important — cost a failed deploy)

Render Free has **no outbound IPv6**. A Supabase *direct* DSN
(`db.<ref>.supabase.co:5432`) resolves to IPv6 and fails with
`Network is unreachable`. **`DATABASE_URL` must be the session pooler**, whose
host is IPv4:

```
postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

The username gains the project ref (`postgres.<ref>`). Get the exact string from
Supabase → **Connect → Session pooler**. Example for an `eu-central-1` project:
`aws-0-eu-central-1.pooler.supabase.com`. Use session mode (port `5432`), not the
transaction pooler (`6543`), so prepared statements work for LangGraph.

## Security posture

- **Storage buckets are private** (`articles`, `sources`, `bodies`, `wiki`). The
  adapters create them private and heal any public bucket back to private; reads
  go through the authenticated service, never a public object URL.
- **All data routes require the bearer token** — `/research`, `/result`,
  `/wiki/query`, `/wiki/ingest`, `/wiki/lint`. Settings fail closed: with no
  `API_TOKEN` and `ALLOW_ANONYMOUS=false`, data routes return 503 and
  `/readiness` reports `auth=false`. Only `/health` and `/readiness` are open
  (the platform probe needs them). Bearer tokens are hashed before they are
  used as rate-limit or quota keys, so raw tokens never reach the database.
- `service_role` (`SUPABASE_SECRET_KEY`) is server-side only and never returned
  in responses. TLS is terminated by Render/Cloudflare.

## Retention (the 500 MB budget)

The free tier is finite, so old run artifacts are pruned automatically:

```
make retention          # dry-run: prints BEFORE, PLANNED, and (with --apply) AFTER
make retention-apply    # perform the deletions
```

`knowledge_engine.scripts.retention` deletes Storage objects older than
`--days` (default 30) while always keeping the `--keep-runs` most recently
updated runs (default 5). It also prunes `service_jobs` older than `--days` and
reports per-bucket MB, the Postgres database size, and warnings above
`--warn-pct` (default 80%) of the free limits — before **and** after. The
curated `wiki` bucket is **never** pruned unless `--include-wiki` is passed.
Checkpoint growth is bounded separately by `CLEANUP_AFTER_RUN=true`.

A weekly job (`.github/workflows/retention.yml`) runs it automatically; set the
repo secrets `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `DATABASE_URL` for it.

## Keepalive (anti-pause + growth)

`.github/workflows/keepalive.yml` runs daily: it wakes the service, checks
`/readiness`, and files one cheap `qa` run so the wiki keeps compounding and the
Supabase project never hits its 7-day idle pause. Set the repo secret
`API_TOKEN` to enable the run step.

## Backups

Supabase Free has no point-in-time recovery. `.github/workflows/backup.yml` dumps
the curated `wiki` bucket to a weekly GitHub artifact
(`knowledge_engine.scripts.backup_wiki --bucket wiki --out wiki-backup`).

## Optional: pgvector semantic tier

Apply `supabase/migrations/20261014120000_wiki_v2_pgvector.sql` to add an
`embedding vector(1024)` column + `wiki.search_pages_semantic(...)` (cosine
nearest-neighbour). Vectors come from
`knowledge_engine.infrastructure.wiki.embedding.embed_text` — a deterministic,
offline stand-in — or a real embedding provider behind the same signature. Free
on Supabase; inactive until the migration is applied.

## Migrations

The image ships `supabase/migrations` and applies pending SQL at boot when
`AUTO_MIGRATE=true` (idempotent, tracked in `schema_migrations`). `/readiness`
also verifies no migration is pending, so a half-migrated schema never reports
ready. The manual equivalents remain available:

```
python -m knowledge_engine.scripts.apply_migrations            # apply
python -m knowledge_engine.scripts.apply_migrations --dry-run  # list pending
```

## One-time setup

1. Confirm the cutover env vars and secrets are set (the wiki schema applies at
   boot via `AUTO_MIGRATE`).
2. In Render: **New → Blueprint** → point at this repo (`render.yaml`).
3. Fill the `sync: false` secrets from your `.env`.
4. Confirm `GET /readiness` is green after the first boot.

## CI/CD

`.github/workflows/deploy.yml` gates on lint/types/unit/evals, then triggers a
Render deploy via the API. Add repo secrets `RENDER_API_KEY` and
`RENDER_SERVICE_ID`; without them the deploy step is skipped (the gate still runs).

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | none | liveness (platform probe) |
| GET | `/readiness` | none | DB + Storage + auth + migrations + providers |
| POST | `/research` | bearer | 202 → poll `/result/{id}` (queued durably, worker-claimed) |
| GET | `/result/{task_id}` | bearer | durable job status (Postgres) |
| GET | `/wiki/query?q=&limit=` | bearer | answer from the wiki, no live call |
| POST | `/wiki/ingest` | bearer | re-ingest a raw run (path must stay under the store root) |
| POST | `/wiki/lint` | bearer | deterministic vault lint |

## Rollback

Undeploy the Render service and unset the cutover switches; local files remain
the cold backup. The Supabase buckets/tables are untouched by rollback.

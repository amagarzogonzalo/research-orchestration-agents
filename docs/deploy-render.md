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
| `API_TOKEN` | optional (secret) | write token for `/research` + wiki mutations |
| `API_TOKEN_READONLY` | optional (secret) | read-only token for `/wiki/query` + `/result` (safe sharing) |
| `RATE_LIMIT_PER_MINUTE` | optional | per-key sliding-window limit on data routes (default 30; `<= 0` disables) |
| `JOB_TIMEOUT_SECONDS` | optional | abort a run after this long (default 900; `<= 0` disables) |
| `CLEANUP_AFTER_RUN` | optional | delete a thread's checkpoints after each run (default true; protects 500 MB) |
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
- **All data routes require the bearer token** — `/research`, `/wiki/query`,
  `/wiki/ingest`, `/wiki/lint`. Only `/health` and `/readiness` are open (the
  platform probe needs them).
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

## One-time setup

1. Apply the wiki schema to the Supabase project (the app only verifies it):
   `supabase db push` or `psql "$DATABASE_URL" -f supabase/migrations/20261012160700_wiki_v2.sql`
   then `... -f supabase/migrations/20261012160701_drop_wiki_v1.sql`.
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
| GET | `/readiness` | none | DB + Storage checks |
| POST | `/research` | bearer | 202 → poll `/result/{id}` |
| GET | `/result/{task_id}` | none | durable job status (Postgres) |
| GET | `/wiki/query?q=&limit=` | none | answer from the wiki, no live call |
| POST | `/wiki/ingest` | bearer | re-ingest a raw run |
| POST | `/wiki/lint` | bearer | deterministic vault lint |

## Rollback

Undeploy the Render service and unset the cutover switches; local files remain
the cold backup. The Supabase buckets/tables are untouched by rollback.

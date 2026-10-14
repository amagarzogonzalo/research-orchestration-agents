# Deploying the HTTP service on Render Free (issue #30)

Target: Render **Free** web service + Supabase **Free** (Postgres + Storage).
Zero-cost, single worker, no browser. The Dockerfile installs only
`--extra store,providers` (no Chromium: 512 MB cannot host it).

## Environment variables to set on Render

Secrets are marked `sync: false` in `render.yaml` and set in the dashboard.
Copy the values from your local `.env` (never commit them).

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes (secret) | Supabase session-pooler DSN, `...:5432?sslmode=require` |
| `SUPABASE_URL` | yes (secret) | Supabase project URL |
| `SUPABASE_SECRET_KEY` | yes (secret) | service_role key (server-side only) |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY` | yes (secret) | free-tier LLM fallback router |
| `OPENALEX_MAILTO`, `CROSSREF_MAILTO` | yes (secret) | scholarly polite pools (real mailbox) |
| `UNPAYWALL_EMAIL` | optional | OA PDF lookup; blank = skip |
| `JINA_API_KEY` | optional | raises the Jina scrape tier |
| `TAVILY_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY` | optional | general-web fallback chain |
| `API_TOKEN` | optional (secret) | bearer token for mutating routes |
| `CHECKPOINTER_KIND` | set by blueprint | `postgres` |
| `STORE_KIND` | set by blueprint | `postgres` |
| `ARTIFACT_STORE_KIND` | set by blueprint | `supabase` |
| `BODY_STORE_KIND` | set by blueprint | `supabase` |
| `WIKI_STORE_KIND` | set by blueprint | `supabase` (or `postgres` for the relational wiki) |
| `WEB_CONCURRENCY` | set by blueprint | `1` (512 MB desk math) |
| `LANGGRAPH_STRICT_MSGPACK` | set by blueprint | `true` (serde allow-list exists) |

`PORT` is provided by Render; the CMD binds it (`${PORT:-10000}`).

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

# HTTP API guide

The service is a thin FastAPI facade over the research engine. It accepts a
research query, runs it through the LangGraph workflow on a durable worker, and
exposes the result as Markdown or JSON.

- Local: `http://127.0.0.1:8000`
- Live: `https://research-orchestration-agents.onrender.com`
  (Render Free: the first request after idle can take ~50 s)
- OpenAPI snapshot: [`docs/openapi.json`](openapi.json)
- Swagger is **disabled by default** in the service. Set `ENABLE_DOCS=true` to
  serve `/docs`, `/redoc`, and `/openapi.json`.

## Authentication

Every data route needs a bearer token:

```bash
export BASE=http://127.0.0.1:8000
export TOKEN=dev
AUTH="Authorization: Bearer $TOKEN"
```

| Variable | Purpose |
|---|---|
| `API_TOKEN` | Write token: `/research`, `/wiki/ingest`, `/wiki/lint`; also accepted on reads |
| `API_TOKEN_READONLY` | Optional read-only token: `/result`, artifact/sources, `/wiki/query`, `/wiki/page`; cannot start runs |
| `ALLOW_ANONYMOUS` | Local development only. With no token and this false, data routes return `503` (fail closed) |

`/health` and `/readiness` are always unauthenticated for platform probes.
Wrong/missing token → `401`; no token configured and anonymous disabled → `503`.

## Run locally

No database, no keys:

```bash
DATABASE_URL= JOBS_KIND=memory AUTO_MIGRATE=false \
  ALLOW_ANONYMOUS=true ENABLE_DOCS=true \
  uv run python bin/api_run.py
```

With Postgres (Supabase session-pooler DSN):

```bash
JOBS_KIND=postgres DATABASE_URL=postgresql://... \
  API_TOKEN=dev ENABLE_DOCS=true \
  uv run python bin/api_run.py
```

`bin/api_run.py` selects the Windows selector event loop automatically (async
psycopg cannot use Proactor). To expose the server on your LAN, set
`HOST=0.0.0.0`; the default bind is `127.0.0.1`.

Docker:

```bash
docker build -t knowledge-engine .
docker run --rm -p 8000:10000 \
  -e API_TOKEN=dev -e ENABLE_DOCS=true \
  knowledge-engine
```

The image listens on `10000` by default; `PORT` overrides it.

## Endpoints

| Method | Path | Auth | Success | Notes |
|---|---|---|---|---|
| GET | `/health` | none | `200 {"status":"ok","engine":"ready"}` | liveness |
| GET | `/readiness` | none | `200 {"ok":bool,"checks":{...}}` | DB, storage, providers, auth, migrations |
| POST | `/research` | write | `202 {"task_id","status":"queued"}` | async; poll `/result/{id}` |
| GET | `/result/{task_id}` | read | `200 JobRecord` | `queued/running/done/error`; `404` unknown |
| GET | `/result/{task_id}/artifact` | read | `200 text/markdown` | `?format=json` for article + sources; `409` if not done |
| GET | `/result/{task_id}/sources` | read | `200 {"task_id","thread_id","sources"}` | `409` if not done |
| GET | `/wiki/query?q=&limit=` | read | `200 {"hits":[...]}` | `limit` 1–20; no live calls |
| GET | `/wiki/page/{slug}` | read | `200 text/markdown` | kebab-case slug; `404` miss |
| POST | `/wiki/ingest` | write | `200 IngestResult` | resolves the artifact by `thread_id`; optional legacy `article_path` |
| POST | `/wiki/lint` | write | `200 LintReport` | deterministic vault lint |

## Submit and poll

```bash
curl -sS -X POST "$BASE/research" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"query":"state of the art in retrieval augmented generation","workflow":"research"}'
```

```json
{"task_id":"6f0d...","status":"queued"}
```

Poll until `done` or `error`:

```bash
TASK=6f0d...
while true; do
  BODY=$(curl -sS "$BASE/result/$TASK" -H "$AUTH")
  echo "$BODY"
  STATUS=$(printf '%s' "$BODY" | python -c "import json,sys;print(json.load(sys.stdin)['status'])")
  case "$STATUS" in done|error) break ;; esac
  sleep 5
done
```

A completed job looks like:

```json
{
  "task_id": "6f0d...",
  "status": "done",
  "thread_id": "6f0d...",
  "query": "state of the art in retrieval augmented generation",
  "workflow": "research",
  "result": {"status": "done", "output_path": "research_store/6f0d.../article.md", "sources": 12},
  "error": null,
  "settings": null
}
```

`output_path` is **server-local**. Use the artifact endpoint from another
process:

```bash
curl -sS "$BASE/result/$TASK/artifact" -H "$AUTH" -o article.md
curl -sS "$BASE/result/$TASK/artifact?format=json" -H "$AUTH" | python -m json.tool
curl -sS "$BASE/result/$TASK/sources" -H "$AUTH" | python -m json.tool
```

## Workflows and per-run settings

`workflow` is one of `research`, `qa`, `summary`, `deep`.

```bash
curl -sS -X POST "$BASE/research" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{
        "query": "compare multi-agent memory architectures",
        "workflow": "deep",
        "thread_id": "my-thread-1",
        "settings": {"max_depth": 2, "max_subqueries": 5}
      }'
```

Settings are validated at submit time and capped by the shared budgets:

| Setting | Range |
|---|---|
| `max_depth` | 0–3 |
| `max_subqueries` | 1–7 |
| `max_iterations` | 0–2 |
| `max_wiki_pages` | 1–20 |
| `thesis` | up to 300 chars |

`thread_id` is optional. If omitted, the server uses the generated `task_id`,
so each request is a separate LangGraph thread. Send your own safe id
(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`) if you want to correlate runs. The
service does not expose HITL resume yet; the review gate auto-approves.

## Wiki

```bash
curl -sS "$BASE/wiki/query?q=retrieval%20augmented%20generation&limit=3" -H "$AUTH"
curl -sS "$BASE/wiki/page/retrieval-augmented-generation" -H "$AUTH"
curl -sS -X POST "$BASE/wiki/ingest" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"thread_id":"my-thread-1","query":"state of the art in RAG"}'
curl -sS -X POST "$BASE/wiki/lint" -H "$AUTH"
```

`/wiki/ingest` without `article_path` loads the rendered artifact from the
artifact store for that `thread_id`. `article_path` remains available for
server-local callers and is still confined to the store root. `/wiki/query`
returns slug/title/score/summary, not server paths.

## Python client

```python
import asyncio
import httpx


async def run(query: str) -> dict:
    headers = {"Authorization": "Bearer dev"}
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as client:
        accepted = await client.post(
            "/research", headers=headers, json={"query": query, "workflow": "research"}
        )
        accepted.raise_for_status()
        task_id = accepted.json()["task_id"]
        while True:
            job = (await client.get(f"/result/{task_id}", headers=headers)).json()
            if job["status"] in {"done", "error"}:
                break
            await asyncio.sleep(5)
        article = await client.get(f"/result/{task_id}/artifact", headers=headers)
        article.raise_for_status()
        return {"job": job, "article": article.text}


print(asyncio.run(run("state of the art in RAG")))
```

Add retries for `429` using the `Retry-After` header; the queue/caller caps are
intentional backpressure, not failures.

## Errors

| Status | Meaning |
|---|---|
| `401` | Missing or invalid bearer token |
| `404` | Unknown task, missing artifact/page, or ingest target |
| `409` | Artifact/sources requested before the job is `done` |
| `422` | Validation error (blank query, unsafe `thread_id`, settings over cap, bad slug) |
| `429` | Rate limit, queue full, or per-caller active-job cap; honor `Retry-After` |
| `500` | Unexpected server error (details are logged, not returned) |
| `503` | Server has no auth configured; use `ALLOW_ANONYMOUS=true` only for local dev |

## Operational limits

- Rate limit: `RATE_LIMIT_PER_MINUTE` per hashed caller (default 30).
- Queue: `MAX_QUEUED_JOBS` (16), `MAX_ACTIVE_JOBS_PER_CALLER` (4),
  `MAX_CONCURRENT_JOBS` (1), `JOB_TIMEOUT_SECONDS` (900), `JOB_MAX_ATTEMPTS` (3).
- Jobs are persisted in Postgres when `JOBS_KIND=postgres`; queued/stale jobs are
  recovered after a restart. With `JOBS_KIND=memory`, jobs are process-local.
- Storage buckets are private; artifacts are served by the API, not public URLs.
- `ENABLE_DOCS=false` in production. Publish `docs/openapi.json` instead:
  `make openapi` regenerates the committed snapshot; `make openapi-check`
  verifies it.

## Known limitations

- Single-user/single-worker design; no multi-tenant accounts.
- No HTTP HITL resume, job cancellation, SSE streaming, or API version prefix
  yet. The library exposes `interrupt`/`resume` and streaming; the HTTP surface
  does not.
- `GET /result` exposes `output_path` for compatibility; external clients should
  use `/artifact` and `/sources`.

# Knowledge Engine — Supabase Migration & Render Free Deployment

**Status:** Draft v0.1
**Author:** Automated from repo audit
**Repo:** `research-orchestration-agents` @ `f285649` (main)
**Target:** Zero-cost production on Render Free + Supabase Free
**Constraint:** 1–3 runs/day, `make check` green at every step

---

## 0. Environment Contract (Single Source of Truth)

All vars defined in `.env.example` must be readable by code. Current gaps:

| Variable | Location | Required For | Notes |
|----------|----------|--------------|-------|
| `GOOGLE_API_KEY` | `engine.py:451` | Google CSE search | Optional — CSE closed to new customers (sunset 2027) |
| `GOOGLE_CX_ENGINE` | `engine.py:452` | Google CSE search | Optional — must pair with key |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `observability/telemetry.py` | OTEL tracing | Default `http://localhost:4318` — add to `.env.example` |
| `SUPABASE_URL` | Phase 1 Storage client | Supabase REST API | In `.env`, not read yet |
| `SUPABASE_SECRET_KEY` | Phase 1 Storage client | Storage write (service_role) | In `.env`, not read yet |

**Action:** Update `.env.example` with the 5 missing entries before Phase 0.

---

## 1. Architecture Decision Record (ADR 0012)

### Decision
Migrate all durable state from `research_store/` (ephemeral FS) to Supabase:

| Layer | Current | Target | Rationale |
|-------|---------|--------|-----------|
| Checkpoints (HITL/resume) | `FileCheckpointer` → JSON files | `AsyncPostgresSaver` → `checkpoints/writes` tables | Survives sleep/redeploy, multi-process |
| Cross-thread memory | `InMemoryStore` | `AsyncPostgresStore` → `store` tables | Shared memory across workers |
| Artifacts (article.md, sources.json) | `ResearchStore` → local files | Supabase Storage buckets `articles/`, `sources/` + Postgres pointer | Ephemeral FS on Render Free |
| Evals cassettes | `evals/cassettes/` (committed) | **Unchanged** — git-tracked, deterministic replay | No migration needed |

### Non-Goals
- Supabase Auth, Realtime, Edge Functions (not used by engine)
- Self-hosted Postgres (Render Free PG expires 30d)
- Browser automation on free tier (OOM risk)

---

## 2. Phase 0 — Connectivity Validation (Day 0)

**Script:** `scripts/probe_supabase.py` (new, gitignored)

```python
#!/usr/bin/env python3
"""Validate Supabase connectivity before any code change."""
import os, asyncio, asyncpg
from supabase import create_client, Client

async def probe_postgres():
    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    version = await conn.fetchval("select version()")
    await conn.close()
    print(f"✅ Postgres: {version[:80]}")

def probe_storage():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SECRET_KEY"]
    client: Client = create_client(url, key)
    # List buckets (requires service_role)
    buckets = client.storage.list_buckets()
    print(f"✅ Storage: {len(buckets)} buckets — {[b.name for b in buckets]}")
    # Ensure target buckets exist or create
    for name in ("articles", "sources"):
        if name not in [b.name for b in buckets]:
            client.storage.create_bucket(name, options={"public": False})
            print(f"  Created bucket: {name}")

if __name__ == "__main__":
    asyncio.run(probe_postgres())
    probe_storage()
```

**Exit criteria:** Both probes print ✅. If fails → fix Supabase project / network / keys before proceeding.

---

## 3. Phase 1 — ResearchStore → Supabase Storage (PR #1)

### Files to Create
```
src/knowledge_engine/store/
├── __init__.py
├── research_store.py          # ← modify
├── file_checkpointer.py       # unchanged
└── supabase_storage.py        # ← new
```

### `supabase_storage.py` Spec
```python
"""Supabase Storage client — upload article.md + sources.json."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from supabase import create_client, Client
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SourceRecord

@dataclass
class SupabaseStorage:
    client: Client
    articles_bucket: str = "articles"
    sources_bucket: str = "sources"

    @classmethod
    def from_env(cls) -> "SupabaseStorage":
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SECRET_KEY"]
        return cls(create_client(url, key))

    def upload_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        content = f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}"
        path = f"{thread_id}.md"
        self.client.storage.from_(self.articles_bucket).upload(
            path, content.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"}
        )
        return self.client.storage.from_(self.articles_bucket).get_public_url(path)

    def upload_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        import json
        payload = json.dumps([s.model_dump(mode="json") for s in sources], indent=2)
        path = f"{thread_id}.json"
        self.client.storage.from_(self.sources_bucket).upload(
            path, payload.encode("utf-8"), {"content-type": "application/json", "upsert": "true"}
        )
        return self.client.storage.from_(self.sources_bucket).get_public_url(path)
```

### `ResearchStore` Changes
- Add optional `storage: SupabaseStorage | None = None` field
- `write_article()` → if `storage`: `storage.upload_article()` + store URL in metadata
- `write_sources()` → if `storage`: `storage.upload_sources()` + store URL in metadata
- **Backward compatible:** local files still written; Storage is additive

### Tests
- `tests/integration/test_supabase_storage.py` (marked `@pytest.mark.live` or `@pytest.mark.supabase`)
- CI: runs only when `SUPABASE_URL` + `SUPABASE_SECRET_KEY` in secrets

### `pyproject.toml` Addition
```toml
[project.optional-dependencies]
supabase = ["supabase>=2.0,<3"]  # ~2MB, no heavy deps
```

---

## 4. Phase 2 — Postgres Checkpointer + Store (PR #2)

### Code Changes (Flips Only)
```python
# In api.py / main.py / wherever ResearchEngine is constructed
engine = ResearchEngine(
    checkpointer_kind="postgres",
    store_kind="postgres",
    database_url=os.environ["DATABASE_URL"],
)
await engine.setup()  # Creates tables on first run
```

### Dependencies
```bash
uv sync --extra store  # pulls psycopg[binary,pool] + langgraph-checkpoint-postgres
```

### Migration Notes
- `langgraph-checkpoint-postgres` creates `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`
- `langgraph-store-postgres` creates `store` table
- No manual migration — `setup()` is idempotent
- Old `research_store/checkpoints/` can be deleted after verified

### Tests
- Existing `tests/integration/test_checkpointer.py` + `test_store_factory.py` cover both kinds
- Add `postgres` parametrize fixture using test DB (Supabase branch or local pg)

---

## 5. Phase 3 — Render Free Deployment (PR #3)

### `Dockerfile`
```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --extra store,providers --frozen

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 10000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "10000"]
```

### `api.py` (New — Thin HTTP Facade)
```python
"""FastAPI wrapper — 202 Accepted + poll pattern for long runs."""
from __future__ import annotations
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from knowledge_engine.engine import ResearchEngine

engine: ResearchEngine | None = None
jobs: dict[str, dict] = {}

class ResearchRequest(BaseModel):
    query: str
    workflow: str = "deep"
    thread_id: str | None = None

class ResearchResponse(BaseModel):
    task_id: str
    status: str = "accepted"

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = ResearchEngine(
        checkpointer_kind="postgres",
        store_kind="postgres",
        database_url=os.environ["DATABASE_URL"],
    )
    await engine.setup()
    yield
    await engine.close()

app = FastAPI(lifespan=lifespan)

@app.post("/research", response_model=ResearchResponse, status_code=202)
async def start_research(req: ResearchRequest, bg: BackgroundTasks):
    task_id = uuid.uuid4().hex[:12]
    jobs[task_id] = {"status": "running", "result": None}
    bg.add_task(_run, task_id, req)
    return ResearchResponse(task_id=task_id)

@app.get("/result/{task_id}")
async def get_result(task_id: str):
    job = jobs.get(task_id)
    if not job:
        raise HTTPException(404, "Task not found")
    return job

async def _run(task_id: str, req: ResearchRequest):
    try:
        result = await engine.research(
            req.query, workflow=req.workflow, thread_id=req.thread_id
        )
        jobs[task_id] = {"status": "completed", "result": result.state.model_dump()}
    except Exception as e:
        jobs[task_id] = {"status": "failed", "error": str(e)}
```

### `render.yaml`
```yaml
services:
  - type: web
    name: knowledge-engine
    env: docker
    plan: free
    dockerfilePath: ./Dockerfile
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: GROQ_API_KEY
        sync: false
      - key: GEMINI_API_KEY
        sync: false
      - key: OPENROUTER_API_KEY
        sync: false
      - key: MISTRAL_API_KEY
        sync: false
      - key: OPENALEX_MAILTO
        sync: false
      - key: CROSSREF_MAILTO
        sync: false
      - key: SEMANTIC_SCHOLAR_API_KEY
        sync: false
      - key: UNPAYWALL_EMAIL
        sync: false
      - key: JINA_API_KEY
        sync: false
      - key: OTEL_TRACES_EXPORTER
        sync: false
      - key: OTEL_METRICS_EXPORTER
        value: "none"
      - key: OTEL_SERVICE_NAME
        value: "knowledge-engine"
      - key: OTEL_EXPORTER_OTLP_ENDPOINT
        sync: false
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_SECRET_KEY
        sync: false
```

### `.dockerignore`
```
.venv
.git
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.hypothesis
evals
tests
research_store
knowledge_base
*.md
*.toml
*.lock
*.yaml
*.yml
devcontainer
.opencode
bin
scripts
docs
```

---

## 6. Phase 4 — CI/CD Pipeline (PR #4)

### `.github/workflows/deploy.yml`
```yaml
name: Deploy to Render
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  test:
    uses: ./.github/workflows/ci.yml  # reuses existing gate

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to Render
        uses: render/deploy-action@v1
        with:
          service-id: ${{ secrets.RENDER_SERVICE_ID }}
          api-key: ${{ secrets.RENDER_API_KEY }}
```

### Secrets Required (Repo → Settings → Secrets)
| Secret | Source |
|--------|--------|
| `RENDER_SERVICE_ID` | Render Dashboard → Service → Settings |
| `RENDER_API_KEY` | Render Account → API Keys |
| All env vars from Phase 0 | Your `.env` values |

### Branch Protection
- Require `make check` passing (CI)
- Require PR review
- No direct push to `main`

---

## 7. Phase 5 — Observability & Runbook (PR #5)

### Health Endpoint
```python
@app.get("/health")
async def health():
    return {"status": "ok", "engine": "ready" if engine else "initializing"}
```

### Structured Logging
- Add `structlog` + JSON output (already in `observability/telemetry.py`)
- Render captures stdout/stderr → Logs tab

### Runbook (Markdown in repo)
```markdown
# Runbook: knowledge-engine on Render Free

## Common Issues
| Symptom | Cause | Fix |
|---------|-------|-----|
| 503 on first request | 15min sleep | Expected — wait 60s, retry |
| OOM 503 | 2 workers / browser extra | `WEB_CONCURRENCY=1`, no `browser` extra |
| DB connection error | Supabase pause (7d idle) | Run probe script / add cron ping |
| 4MB payload limit | Large article | Chunk or compress |

## Scaling Triggers
- >5 concurrent deep runs → upgrade Render to Starter ($7/mo)
- >500MB DB → Supabase Pro ($25/mo)
- >5GB egress → Supabase Pro
```

---

## 8. Acceptance Criteria (Definition of Done)

| Phase | Gate |
|-------|------|
| 0 | Probe script prints both ✅ |
| 1 | `make check` green + `article.md` visible in Supabase Storage bucket |
| 2 | `make check` green + `research()` resumes after `docker stop/start` |
| 3 | `curl POST /research` → 202 → `GET /result/{id}` returns completed state |
| 4 | Push to `main` → Render deploy → health check passes |
| 5 | Runbook covers top 5 failure modes; team can follow without author |

---

## 9. Rollback Plan

| Phase | Rollback |
|-------|----------|
| 1 | Remove `storage` from `ResearchStore`; local files only |
| 2 | `checkpointer_kind="file"`, `store_kind="memory"`; `await engine.close()` |
| 3 | Delete Render service; run locally |
| 4 | Disable workflow |
| 5 | Delete runbook |

---

## 10. Next Immediate Action

```bash
# 1. Fix .env.example (add 5 missing vars)
# 2. Run Phase 0 probe
uv run python scripts/probe_supabase.py
```

Once both ✅, open PR for Phase 1.

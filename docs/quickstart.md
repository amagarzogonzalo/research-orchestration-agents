# Quickstart — try the Knowledge Engine in ~5 minutes

Two ways in: **offline (no keys, no network)** and **online (the deployed
service)**. Pick whichever suits you.

## 0. Prerequisites

- Python **3.11 or 3.12** and [uv](https://docs.astral.sh/uv/).
- Clone the repo (ask the owner for access — it is private):

  ```bash
  git clone git@github.com:amagarzogonzalo/research-orchestration-agents.git
  cd research-orchestration-agents
  ```

## 1. Offline — prove it works, zero keys (~2 min)

```bash
make install      # uv sync --extra dev
make test-fast    # unit suite (offline)
make bench        # research + deep workflows on deterministic fakes; prints metrics
make wikibench    # the LLM-wiki benchmark (deterministic, no LLM)
make evals        # replays recorded cassettes bit-identically
```

No API keys required. `make wikibench` writes `bench/wikibench/report.md`;
`make bench` prints supersteps/RAM for the `research` and `deep` workflows.

> **No `make`?** (Windows without GNU make, etc.) run the underlying commands:
>
> ```bash
> uv sync --extra dev                                     # make install
> uv run pytest tests/unit -q                             # make test-fast
> uv run python -m knowledge_engine.scripts.bench         # make bench
> uv run python -m knowledge_engine.scripts.wikibench     # make wikibench
> uv run python evals/runner.py                           # make evals
> uv run python bin/api_run.py                            # make serve
> ```

The **full gate** (lint, types, arch, semgrep, tests, evals, benchmarks):

```bash
make check
```

## 2. Online — the deployed service

- Base URL: <https://research-orchestration-agents.onrender.com>
- Swagger is **disabled** in production (`/docs` returns 404). The full endpoint
  guide and copy-paste commands are in [`docs/api.md`](api.md); the committed
  OpenAPI snapshot is [`docs/openapi.json`](openapi.json).
- Open, no token: `GET /health`, `GET /readiness`.
- Data routes need a bearer token (ask the owner):

  ```bash
  BASE=https://research-orchestration-agents.onrender.com
  curl -H "Authorization: Bearer $TOKEN" "$BASE/wiki/query?q=retrieval%20augmented%20generation"

  curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
       -d '{"query":"state of the art in RAG","workflow":"research"}' "$BASE/research"
  # -> 202 {"task_id": "..."}; then poll:
  curl -H "Authorization: Bearer $TOKEN" "$BASE/result/<task_id>"
  ```

  `workflow` is one of `research`, `qa`, `summary`, `deep`. The first request
  after idle takes ~50 s (Render Free cold start).

## 3. Run the service locally

No database and no keys (memory job store, anonymous local access):

```bash
DATABASE_URL= JOBS_KIND=memory AUTO_MIGRATE=false \
  ALLOW_ANONYMOUS=true ENABLE_DOCS=true \
  uv run python bin/api_run.py
# http://127.0.0.1:8000  (Swagger at /docs because ENABLE_DOCS=true)
```

With Postgres and a token:

```bash
cp .env.example .env   # fill DATABASE_URL + free-tier LLM keys
API_TOKEN=dev ENABLE_DOCS=true uv run python bin/api_run.py
```

`/health` returns 200 either way. Without cloud storage/providers,
`/readiness` reports those checks as `false`. Real research needs LLM keys; use
the offline fakes via `make bench` for zero-cost workflow checks. Full HTTP
commands: [`docs/api.md`](api.md).

## Where to go next

| I want to… | Read |
|---|---|
| Use the HTTP API from curl/Python | [`docs/api.md`](api.md) |
| Understand the architecture | `docs/plans/*`, `SPEC.md` |
| Deploy my own instance | `docs/deploy-render.md` |
| Contribute | `CONTRIBUTING.md` |
| Understand the wiki layer | `docs/LLM_WIKI.md` |
| See the benchmark | `bench/wikibench/README.md` |

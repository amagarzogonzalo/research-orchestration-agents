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

The **full gate** (lint, types, arch, semgrep, tests, evals, benchmarks):

```bash
make check
```

## 2. Online — the deployed service

- Interactive API docs (Swagger): <https://research-orchestration-agents.onrender.com/docs>
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

```bash
cp .env.example .env   # optional: fill free-tier LLM keys for live research
make serve             # http://127.0.0.1:8000  (Swagger at /docs)
```

Without keys it boots with local `file/memory/local` persistence: `/health`
returns 200, `/readiness` reports the cloud checks as `false`, and `/research`
needs LLM keys (use the offline fakes via `make bench` instead).

## Where to go next

| I want to… | Read |
|---|---|
| Understand the architecture | `docs/plans/*`, `SPEC.md` |
| Deploy my own instance | `docs/deploy-render.md` |
| Contribute | `CONTRIBUTING.md` |
| Understand the wiki layer | `docs/LLM_WIKI.md` |
| See the benchmark | `bench/wikibench/README.md` |

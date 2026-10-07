---
@id: "plans/0000"
type: ADR-INDEX
status: ACCEPTED
date: 2026-09-03
provenance:
  - docs/instruction.md
  - docs/information.md
  - docs/tools.md
---

# 0000 — Autonomous Knowledge Curation Engine: Implementation Plan Index

## Status
Planning baseline. This index is the entry point for the engineering team.

## 1. Document Map

| Doc | Title | Concern |
|-----|-------|---------|
| `0001-architecture.md` | Hexagonal Core + Event-Driven Shell | Ports/adapters, bounded context, message topology |
| `0002-schemas.md` | Pydantic Domain Schemas | Contracts between ports across the broker |
| `0003-rate-limiting.md` | Token-Bucket Queueing | Backpressure vs free-tier RPM (429 discipline) |
| `0004-fallback-routing.md` | Multi-Provider Fallback Router | Circuit breaking, failover cascade, model affinity |
| `0005-orchestration.md` | LangGraph Orchestration Graph | State machine, checkpointing, evaluator-optimizer |
| `0006-observability.md` | OpenTelemetry GenAI Observability | Tracing, token/cost/latency metrics, events |
| `0007-event-driven.md` | Event-Driven Choreography | Station topology, idempotency, HITL review gate (runtime) |

## 2. Verified Dependency Baseline (live-checked 2026-09-03)

All version pins below were resolved against the live PyPI + GitHub APIs during planning. Common floor: **Python >= 3.10**; the two memory/guardrail libs cap at `<3.14`, so adopt **3.11/3.12** as the runtime to hold the union.

| Package | Pin | Why / Role |
|---------|-----|-----------|
| `langgraph` | `1.2.x` | Orchestration graph, supervisor, checkpoint integration |
| `langgraph-checkpoint-postgres` | `3.1.x` | Durable Postgres checkpointer (Neon) |
| `langgraph-checkpoint` | `4.2.x` | Core checkpoint abstraction |
| `letta` | `0.16.x` | Memory controller (MemGPT lineage); requires `>=3.11,<3.14` |
| `crawl4ai` | `0.9.x` | DOM -> Markdown LLM-ready scraping (async, Playwright-backed) |
| `trafilatura` | `2.2.x` | Browserless fast text extraction |
| `instructor` | `1.16.x` | Retry-able structured extraction -> Pydantic |
| `guardrails-ai` | `0.11.x` | Structural output validation (Pydantic-based) |
| `mcp` | `2.1.x` | MCP server/client SDK for the federated search + scraping ports |
| `litellm` | `1.99.x` | Unified provider gateway + router primitives |
| `openai` | `3.7.x` | Underlying client (used by many integrations) |
| `redis` | `8.1.x` | Redis Streams broker + token buckets + Redis checkpointer |
| `psycopg` | `3.3.x` | Neon/Postgres driver |
| `qdrant-client` | `1.19.x` | Vector store adapter |
| `tenacity` | `9.1.x` | Retry/backoff primitives (429/5xx) |
| `opentelemetry-sdk` | `1.44.x` | Tracing/metrics SDK |
| `groq` | `1.7.x` | Groq LPU adapter |

> Commit a frozen `requirements.txt` / `uv.lock` against these majors. Free tiers change RPM/SLA without notice; **pin, do not float**, and keep adapter code behind ports so bumps are isolated.

## 3. Reference Open-Source Codebases (live-checked 2026-09-03)

These are the canonical OSS implementations to read before writing our own code:

| Repo | Stars | What to copy / avoid |
|------|-------|----------------------|
| `langchain-ai/langgraph` | 41k | Checkpoint lifecycle, supervisor pattern, interrupt semantics |
| `letta-ai/letta` | 24.6k | Memory paging — the `agent` self-editing memory-blocks model |
| `unclecode/crawl4ai` | 81.2k | Async scraper + Markdown pipeline + cache strategy |
| `BerriAI/litellm` | 58k | Provider router: `completion`, `router`, fallback handling |
| `modelcontextprotocol/python-sdk` | 24.2k | FastMCP server/client patterns, tool/resource/prompt primitives |
| `modelcontextprotocol/servers` | 90k | Reference MCP servers (patterns for our scraping/search servers) |
| `openags/paper-search-mcp` | 2.6k | Federated scholarly search across arXiv/PubMed/bioRxiv — closest MCP skeleton to our need |
| `jina-ai/reader` | 11.9k | `r.jina.ai` prefix — URL -> LLM input; alternative to crawl4ai |
| `openai/openai-python` | 31.5k | Responses/structured-output API surface |

## 4. Zero-Cost & Quota Discipline Rules (non-negotiable)

1. **OpenAlex**: individual record lookups by ID/DOI are **free & un-metered**. Batch search consumes credits. Prefer DOI/ID-first resolution; only fall back to search.
2. **Semantic Scholar**: 1k req/s **shared public** limit — treat as a 429 magnet. Never fire parallel bursts; route through the token bucket and circuit breaker. (Planning session observed a live `429` from this API — treat it as nominal.)
3. **OpenRouter**: 28+ free models at ~20 RPM, no SLA. Always behind the fallback cascade, never first-choice for bulk.
4. **Mistral Experiment**: ~1B token/month ceiling — reserve for high-volume synthesis.
5. **Gemini Flash**: ~1M context — reserve for document synthesis only.
6. **Never** route a paid tier (OpenAI paid, Anthropic, Datadog, LangSmith). Every paid recommendation is out of scope by project rule.

## 5. Decision Log Summary

- **AD-1** Hexagonal core with **Redis Streams** as the sole inter-service transport. Rationale: persistence, consumer groups (XACK), no paid broker.
- **AD-2** All LLM/provider I/O behind a `ProviderPort` + `FallbackRouter`. Never import a vendor SDK into the core.
- **AD-3** Structured output via **Instructor** -> Pydantic; guardrails-ai as a secondary structural validator on critical documents.
- **AD-4** Runtime is **event choreography**: independent stations over Redis Streams (ADR 0007); LangGraph supplies legal transitions + durable Postgres checkpointer (Neon). Redis Streams is the event shell.
- **AD-5** OTel GenAI semantic conventions for token/cost/latency; JSONL exporting to a free filesystem path (no Datadog).

## 6. Build Order (dependencies)

1. `0002-schemas` — contracts (everything depends on it; build first.)
2. `0003-rate-limiting` — token bucket + broker wiring
3. `0004-fallback-routing` — provider port + router + circuit breaker
4. Ports/adapters: search, scrape, vector, memory
5. `0007-event-driven` — station choreography wired to the ports (runtime)
6. `0006-observability` — instrument alongside, not after
7. Standardized Markdown/YAML output stage (Schema.org frontmatter)

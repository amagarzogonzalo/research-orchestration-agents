---
@id: "plans/0001"
type: ADR
status: ACCEPTED
date: 2026-09-03
supersedes: null
related:
  - 0000
---

# 0001 — Hexagonal Core + Event-Driven Shell

## Context

The pipeline ingests ambiguous, noisy, rate-limited external resources (scholarly APIs, LLM providers, web scrapers) and must produce deterministic, schema-valid research documents. Provider SDKs churn (the `langchain-openai` 1.x vs 0.x breakage alone justifies isolation); free-tier endpoints have no SLA. We need the domain logic to be:

- **Ignorant of transport** (which provider, which scraper, which DB).
- **Drivable** asynchronously so a 429 degrades gracefully instead of failing synchronously.
- **Replayable/persistable** so state survives process death.

## Decision

Adopt **Hexagonal (Ports & Adapters) architecture** wrapped in a **Redis Streams event shell**. The core exposes *ports* (abstract interfaces) and imports *adapters* (concrete implementations) only at the composition root. All cross-boundary messages flow over Redis Streams, not direct method calls.

## Ports (Core-facing contracts)

| Port | Method sketch | Owned by |
|------|---------------|----------|
| `SearchPort` | `async search(query) -> list[SourceRecord]` | federated scholarly search (OpenAlex/S2/Crossref/arXiv) |
| `FetchPort` | `async fetch(doi) -> Optional[SourceRecord]` | DOI/ID-first lookup (free OpenAlex path) |
| `ScrapePort` | `async to_markdown(url) -> DocumentBody` | crawl4ai / trafilatura |
| `ExtractPort` | `async extract(model, Task) -> T` | structured output via provider port |
| `ProviderPort` | `async complete(req: ChatRequest) -> ChatResult` | **the** LLM transport; variant methods for cheap/rich routing |
| `VectorPort` | `upsert/query` | Qdrant |
| `MemoryPort` | `read/write/forget` | Letta memory controller |
| `StorePort` | `put/get` | Neon/Postgres knowledge base |

Each port carries its own `Input`/`Output` Pydantic types (see `0002-schemas.md`) — never leak a vendor type across a port boundary.

## Adapters (at the edges)

- `OpenAlexSearchAdapter`, `SemanticScholarAdapter`, `CrossrefAdapter`, `ArxivAdapter`
- `Crawl4aiAdapter`, `TrafilaturaAdapter`
- `OpenRouterAdapter`, `GroqAdapter`, `GeminiAdapter`, `MistralAdapter`, `OllamaAdapter`
- `QdrantAdapter`, `NeonStoreAdapter`, `RedisStreamBroker`

## Composition Root / Dependency Injection

Adapters are selected in `app.py` / `worker.py` via a `Container` (plain modules or a light DI lib). The core never imports adapters; the graph is assembled once at boot:

```python
container = Container(
    provider=FallbackRouter(providers=[GroqAdapter(), OpenRouterAdapter(), GeminiAdapter(), MistralAdapter(), OllamaAdapter()]),
    search=DemuxSearchAdapter([OpenAlexAdapter(), SemanticScholarAdapter(), CrossrefAdapter(), ArxivAdapter()]),
    scrape=Crawl4aiAdapter(),
    store=NeonStoreAdapter(),
    memory=LettaAdapter(),
    vector=QdrantAdapter(),
    broker=RedisStreamBroker(url=settings.REDIS_URL),
)
```

## Broker Topology (Redis Streams)

- Streams are the **write-ahead event log** of the pipeline. Consumer groups give load balancing; `XACK` gives exactly-once-ish delivery.
- A failed step writes an `errored` event (with the Pydantic payload + failure envelope) and **does not** auto-retry forever — it returns to an `inbox` for the circuit breaker to re-admit.
- Reason: Redis provides persistence + consumer groups at **$0**; a real message-broker subscription (Kafka/Confluent/SQS) is paid and thus out of scope.

## Consequences

- **Cost**: adding a provider or scraper is a new adapter, zero core churn.
- **Testability**: ports are Mock-able; the domain can be unit-tested with zero network.
- **Risk**: more files/indirection than a monolith — acceptable given the resilience mandate.

## References
- `docs/information.md` "Embracing Distributed Systems Principles for AI" (LLMs are unreliable endpoints).
- Pattern source: Alistair Cockburn; applied by LangChain/LangGraph production guidance.

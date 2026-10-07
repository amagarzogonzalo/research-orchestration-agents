---
@id: "plans/0002"
type: ADR
status: ACCEPTED
date: 2026-09-03
related:
  - 0001
  - 0005
---

# 0002 — Pydantic Domain Schemas (Cross-Port Contracts)

## Context

Every boundary message (across Redis Streams and between ports) must be a versioned, validated `BaseModel`. Pydantic v2 + Instructor gives us retry-driven structured extraction that round-trips to the same schema used on ingest. This is the single source of truth the whole pipeline builds against — build these first.

## Conventions
- Every model: `model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")`.
- Every enumerated field: `Literal` or `StrEnum` — never free strings that can drift.
- A top-level `@id`/`type` on persisted artifacts to satisfy Schema.org-style knowledge-base output.

## Domain Models

### 1. Provider transport

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from enum import StrEnum

class Role(StrEnum):
    system = "system"; user = "user"; assistant = "assistant"; tool = "tool"

class ChatMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None

class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = 1024
    # routing-affine tag used by the FallbackRouter (0004)
    affinity: Literal["micro", "macro", "million"] = "macro"

class ChatResult(BaseModel):
    message: ChatMessage
    usage: "UsageSummary"
    provider: str          # which adapter served it
    model: str             # actual model id
    meta: dict = Field(default_factory=dict)
```

### 2. Cost/usage telemetry

```python
class UsageSummary(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0   # recomputed by a pricing table, never trusted from vendor
```

### 3. Search / source model (federated scholarly)

```python
class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                       # canonical id, e.g. openalex:W716...
    doi: str | None = None
    title: str
    source: Literal["openalex","semantic_scholar","crossref","arxiv"]
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    pdf_url: str | None = None
    citation_count: int = 0
    tldr_llm: str | None = None     # 50-token summary for downstream filtering
    fetched_at: str | None = None
```

### 4. Scraped content

```python
class DocumentBody(BaseModel):
    @id: str | None = None
    url: str
    title: str
    markdown: str                     # LLM-ready, token-reduced via crawl4ai heuristic
    char_count: int = 0
    crawl_method: Literal["crawl4ai","trafilatura"] = "crawl4ai"
    captured_at: str | None = None
```

### 5. Extraction task (Instructor-bound)

```python
class ExtractionRequest(BaseModel):
    task_id: str
    kind: Literal["bool_filter","entity_extract","summary_50","synthetic_multi_doc"]
    source: SourceRecord | DocumentBody
    schema_ref: str                   # target Pydantic model name for Instructor
    retries: int = 2
```

### 6. Orchestration state (LangGraph `GraphState`)

```python
class GraphState(BaseModel):
    thread_id: str
    query: str
    sources: list[SourceRecord] = Field(default_factory=list)
    bodies: list[DocumentBody] = Field(default_factory=list)
    filtered: list[SourceRecord] = Field(default_factory=list)
    synthetic_notes: list[str] = Field(default_factory=list)
    judge_score: float | None = None
    output_path: str | None = None
    status: Literal["running","awaiting_review","done","failed"] = "running"
```

### 7. Standardized knowledge-base artifact (output stage)

```python
class KnowledgeArtifact(BaseModel):
    @id: str
    type: Literal["KnowledgeBaseArticle"] = "KnowledgeBaseArticle"
    title: str
    provenance: list[SourceRecord] = Field(default_factory=list)
    body_markdown: str
    frontmatter_yaml: str            # serialized @id, type, provenance, citations
    citations: list[str] = Field(default_factory=list)
    tldr_llm: str          # ~50 words, lets future RAG eval skip the body
    generated_at: str
```

## Versioning Rule
- Breaking schema changes bump a `schema_version` byte in the stream message envelope. Consumers tolerate their own known version and reject unknown ones back to the dead-letter rather than silently parsing wrong.

## Integration Snippet (Instructor bound to the broker)

```python
import instructor
import redis.asyncio as redis

async def extract(client, req: ExtractionRequest, target_model: type[BaseModel]):
    # client = litellm/Gemini client wrapped by Instructor
    result, raw = client.messages.create_with_completion(  # instructor retry loop
        response_model=target_model,
        messages=request_messages(req),
        max_retries=req.retries,
    )
    rc = redis.from_url(SETTINGS.REDIS_URL)
    await rc.xadd("extraction.done", as_envelope(target_model, result))  # typed outbound
    return result
```

## References
- `docs/instruction.md` §OpenTelemetry token accounting + central "knowledge base" claims.
- Pydantic v2 docs + Instructor `create_with_completion` retry semantics (pin `instructor==1.16.x`).

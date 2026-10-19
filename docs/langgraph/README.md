# LangGraph Technical Documentation

Deep-dive reference for how the orchestrator layer of this project is designed,
based on **live-verified primary sources** (official LangChain/LangGraph docs and
the `langchain-ai/langgraph` repository).

## Documents

| Doc | Purpose |
|-----|---------|
| [Architecture & best practices](architecture.md) | Which software architecture to use (hexagonal vs layered vs clean), the official LangGraph application structure, node/state design, error-handling taxonomy, and what we learn from professional codebases |
| [ADR-0008 — LangGraph orchestrator](../plans/0008-langgraph-orchestrator.md) | The decision record: event-driven *internally* via LangGraph, minimal external surface, files-only checkpointer |
| [ADR-0009 — Portable research library](../plans/0009-portable-research-library.md) | Files-only persistence, browser automation, free-LLM discovery (current) |

## Verified package baseline

| Package | Version | Role |
|---------|---------|------|
| `langgraph` | `>=1.2,<2` | Graph runtime (Pregel), `StateGraph`, `interrupt`, `Send`, `Command`, `RetryPolicy`, `TimeoutPolicy`, `error_handler` |
| `langgraph-checkpoint` | `>=4.2,<5` | Checkpointer base + `InMemorySaver` (tests) |
| `langchain[mcp]` | `>=1.4,<2` | `MCPAdapter` — discover FastMCP tools on in-process servers |
| `mcp` | `>=2.1,<3` | MCP server SDK (FastMCP) |

> **Checkpoint persistence:** `memory` (tests), `file`
> (`FileCheckpointer`, `store/file_checkpointer.py` — JSON under
> `research_store/checkpoints/`), or `postgres`
> (`AsyncPostgresSaver`, opt-in; production uses it via `DATABASE_URL`).
> No SQLite and no Redis. The file kind stays the portable default; the cloud
> kind is selected by `CHECKPOINTER_KIND=postgres`.
>
> **Wiki layer (files-first, ports-only):** the compiled wiki lives under
> `<store_root>/wiki` as plain markdown (sources/concepts/entities/queries/
> synthesis + `index.md`/`log.md`). The `.wiki.db` FTS5 sidecar
> (`infrastructure/wiki/sidecar.py`) is a **derived, deletable cache** (rebuild
> = identical content), not a checkpoint store. Cloud mirrors
> (`SupabaseWikiStore`/`SupabaseVaultLinter`) are optional adapters behind
> `core/ports.py` — the engine never imports them by default. See
> `docs/LLM_WIKI.md` and `docs/plans/0017–0030`.

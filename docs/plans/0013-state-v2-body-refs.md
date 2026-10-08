---
@id: "plans/0013"
type: ADR
status: ACCEPTED
date: 2026-10-07
related:
  - 0008   # LangGraph orchestrator (state design this amends)
  - 0009   # portable research library
  - 0012   # artifact store port (Supabase Storage is the future BodyStoragePort adapter)
---

# 0013 — State v2: body references instead of inline markdown

## Context

`WorkflowState.bodies` accumulated full `DocumentBody` records (10-50KB of
markdown each) under the `add` reducer. LangGraph checkpoints copy the whole
channel state at every superstep, so a deep run with ~16 supersteps and 10+
bodies produced MB-scale checkpoint blobs — memory pressure on the 512MB RAM
budget and slow serde on every step.

## Decision

1. New core port `BodyStoragePort` (`save(DocumentBody) -> BodyRef`,
   `load(BodyRef) -> str`); adapter `store/body_file_store.py:
   LocalBodyStorage` writes UTF-8 markdown under
   `<store_root>/bodies/<sha256(url)[:16]>.md` — deterministic, idempotent,
   portable (the folder moves with `research_store/`).
2. `FetchBodyNode` persists each body through the port and writes a single
   `BodyRef` (~150 bytes: url/title/path/char_count/crawl_method) into the
   `bodies` channel; raw markdown never enters state.
3. `SynthesizeNode` materializes refs through the port before prompt
   building; `DocumentBody` and legacy dict snapshots pass through — the
   prompt formatter sees one canonical shape, so prompt bytes are identical
   across both state eras.
4. Replay neutrality is PROVEN, not asserted: a golden-master pins prompt
   bytes (W3), a byte-equality test proves ref-path == inline-path, and eval
   cassettes replay bit-identically with zero re-records.
5. `load()` refuses any reference resolving outside the storage root — ref
   strings round-trip through checkpoints, so containment is enforced even
   against tampered state (path-traversal guard).

## SOLID mapping

| Principle | Application |
|-----------|-------------|
| SRP | `FetchBodyNode` persists; `SynthesizeNode` reads; state carries pointers. |
| OCP | A Supabase-Storage body adapter is a new file implementing the port — zero node changes. |
| LSP | Every `BodyStoragePort` implementation passes the same contract tests (`test_body_storage.py`). |
| ISP | Nodes see only `save`/`load`; no filesystem, no backend specifics. |
| DIP | Nodes depend on the port; only `engine._nodeset` (composition root) chooses `LocalBodyStorage`. |

## Consequences

- **Positive:** checkpoint blobs shrink by orders of magnitude; RAM stays
  inside the 512MB ceiling; bodies are inspectable files; provenance travels
  in the ref (url/title survive without loading).
- **Negative:** bodies on disk are an additional file set to move with the
  store; the legacy dict-snapshot branch in `_materialize_bodies` must be
  kept until a migration sweep confirms no old threads remain.
- **Zero-cost:** local files only; Supabase Storage stays an opt-in adapter
  (ADR 0015 track).

## References

- `tests/unit/test_prompt_golden_master.py` — the tripwire.
- `tests/integration/test_state_v2_wiring.py` — the byte-equality proof.
- `evals/README.md` — the re-record loop this decision avoids.

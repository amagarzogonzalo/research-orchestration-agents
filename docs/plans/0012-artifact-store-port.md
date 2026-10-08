# 0012 — Durable artifact store: port + adapters (Supabase Storage)

**Status:** Proposed · **Date:** 2026-10-07 · **Type:** architecture + delivery
**Tracking issue:** #5 · **Related:** ADR 0008 (LangGraph orchestrator), ADR 0009 (portable library), `SPEC.md`

## Context

`research_store/` is ephemeral on every free host (Render Free has no persistent
disk). Today `ResearchStore.write_article()` / `write_sources()`
(`store/research_store.py:154,144`) write directly to disk, so published output
dies on redeploy/sleep. Checkpoints and cross-thread memory already have a
postgres path via `orchestration/checkpoint.py:45` and
`orchestration/store_factory.py:43`; **published artifacts do not**.

Bolting a `if supabase:` branch into `ResearchStore` would violate SRP (one class
writing to two stores), OCP (every new backend edits the class), and DIP (core
depending on the `supabase` SDK). The hexagonal architecture already answers
this: **a new core port + interchangeable adapters.**

## Decision

Introduce `ArtifactStorePort` in `core/ports.py`; make `ResearchStore` delegate
published-artifact persistence to it; ship `LocalArtifactStore` (preserves
today's on-disk behavior) and `SupabaseArtifactStore` (new). Wire the adapter in
the composition root (`engine.py`), never in the store.

### SOLID mapping (why this shape)

| Principle | Application |
|-----------|-------------|
| **SRP** | `ResearchStore` owns *orchestration + local run folders*; the adapter owns *where published artifacts live*. One reason to change each. |
| **OCP** | A new backend = a new adapter file. `ResearchStore` is untouched. |
| **LSP** | One **contract test suite** (below) runs against every adapter; any adapter that passes is a drop-in substitute. |
| **ISP** | Port exposes only what callers need: `put_article`, `put_sources`, `get_article`. No list/delete/auth surface leaks in. |
| **DIP** | `ResearchStore` and nodes depend on `ArtifactStorePort`; only `engine.py` (composition root) imports `supabase`. |

### Port (core, depends on schemas only — satisfies tach.toml:29)

```python
# core/ports.py
class ArtifactStorePort(abc.ABC):
    """Durable store for published artifacts (article + sources)."""

    @abc.abstractmethod
    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        """Persist the rendered article; return a locator (path or URL)."""

    @abc.abstractmethod
    def put_sources(self, thread_id: str, sources: Sequence[SourceRecord]) -> str:
        """Persist resolved sources; return a locator (path or URL)."""

    @abc.abstractmethod
    def get_article(self, thread_id: str) -> str | None:
        """Return the article markdown, or None when absent."""
```

> The port is **sync** to match `ResearchStore`'s current synchronous surface;
> `supabase-py` is sync. Async can be added later without breaking LSP callers
> that already tolerate blocking (file writes are already sync). Documented as a
> deliberate trade-off, revisit if an async backend lands.

### Adapters

| Adapter | Location | Notes |
|---------|----------|-------|
| `LocalArtifactStore` | `store/artifact_store.py` | Same bytes as today: `<root>/<thread>/article.md`, `sources.json`. Default. |
| `SupabaseArtifactStore` | `store/supabase_artifact_store.py` | `supabase-py` storage client; buckets `articles`, `sources`; upsert; returns public URL. |
| `FakeArtifactStore` | `tests/conftest.py` | In-memory dict; used by every offline test. |

`store/` is the correct home: it is the persistence-adapter layer (tach.toml:26)
and already depends on `core` + `schemas`; external SDK imports don't cross a
module boundary.

## TDD plan (Red → Green → Refactor, `make check` green after every step)

### Test pyramid
```
        e2e: research() with Supabase port → article URL resolves   (tests/e2e, live)
      integration: SupabaseArtifactStore round-trip                 (tests/integration, @live)
    contract: every adapter must pass the SAME behaviour suite       (tests/unit/store)
  unit: ResearchStore delegates to Port; FakeArtifactStore is a Spy  (tests/unit/store)
```

### Step 0 — Red: contract suite + fake (no production code yet)
`tests/unit/store/test_artifact_store_contract.py`
```python
ADAPTERS = [FakeArtifactStore, LocalArtifactStore]          # Supabase appended when keys present

@pytest.mark.parametrize("factory", ADAPTERS)
def test_put_then_get_article_roundtrips(factory, tmp_path):
    store = factory(tmp_path)
    url = store.put_article("t1", _artifact("hello"))
    assert "t1" in url
    assert store.get_article("t1") == "hello"

@pytest.mark.parametrize("factory", ADAPTERS)
def test_put_sources_is_idempotent(factory, tmp_path):
    store = factory(tmp_path)
    a = store.put_sources("t1", _sources()); b = store.put_sources("t1", _sources())
    assert store.get_article("missing") is None
```
Runs RED (types/classes don't exist). This suite *is* the LSP proof.

### Step 1 — Green: define `ArtifactStorePort` + `LocalArtifactStore`
- Add port to `core/ports.py`; export via `core/__init__` if the package re-exports.
- Implement `LocalArtifactStore` to reproduce `article.md`/`sources.json` bytes.
- `FakeArtifactStore` in `conftest.py`.
- **Gate:** contract suite green for both.

### Step 2 — Red→Green: `ResearchStore` depends on the port (DIP)
`tests/unit/store/test_research_store_delegates.py`
```python
def test_write_article_delegates_and_keeps_local_copy(tmp_path):
    spy = SpyArtifactStore()
    store = ResearchStore(tmp_path, artifacts=spy)
    store.write_article(_artifact("body"), "t1")
    assert spy.article_calls == [("t1", "body")]        # delegated
    assert (tmp_path / "t1" / "article.md").exists()    # backward compatible
```
- Change `ResearchStore.__init__(root, artifacts: ArtifactStorePort | None = None)`;
  default to `LocalArtifactStore(root)` when `None` (no behavior change).
- `metadata.json` records the returned locator (`artifact_url`).

### Step 3 — Red→Green: `SupabaseArtifactStore` (integration)
`tests/integration/test_supabase_artifact_store.py` (marker `live`)
```python
@pytest.mark.live
def test_supabase_roundtrip():
    store = SupabaseArtifactStore.from_env()            # skips if keys absent
    store.put_article("ci-probe", _artifact("x"))
    assert store.get_article("ci-probe") == "x"
```
- Add `supabase.*` to the mypy vendor-override list (pyproject.toml:112).

### Step 4 — Green: composition root wiring (`engine.py`)
- `ResearchEngine(..., artifact_store_kind: str = "local")`; `"supabase"` builds
  the adapter from `SUPABASE_URL`/`SUPABASE_SECRET_KEY` and passes it into
  `ResearchStore`. Missing keys → `ConfigurationError` (typed, `errors`-style).

### Step 5 — e2e + config
- `tests/e2e`: `research()` with the Supabase port → `get_article(thread)` resolves.
- `.env.example` complete (done); `SPEC.md` Phase 1 closes.

## File map

```
core/ports.py                          # +ArtifactStorePort
store/artifact_store.py                # +LocalArtifactStore
store/supabase_artifact_store.py       # +SupabaseArtifactStore
store/research_store.py                # ctor takes ArtifactStorePort | None
store/__init__.py                      # export new adapters
engine.py                              # composition root: artifact_store_kind
pyproject.toml                         # extras: supabase = [...]; mypy override
tests/conftest.py                      # +FakeArtifactStore
tests/unit/store/test_artifact_store_contract.py
tests/unit/store/test_research_store_delegates.py
tests/integration/test_supabase_artifact_store.py
```

## Gates & hard rules

- `make check` green each step (lint, types, arch, semgrep, agents, context, test, evals).
- Google docstrings on every new module/class/method (`skills/documenting-python`).
- New dependency `supabase` added as an **opt-in extra** and requested per
  AGENTS.md rule 4 (already agreed with the owner).
- No secret in git; keys only via Render/Supabase env (AGENTS.md rule 1).
- Nodes stay pure; adapters are the only I/O.
- Backward compatible: default path byte-identical, so `evals/` snapshots hold.

## Acceptance (DoD)

| Check | Evidence |
|-------|----------|
| Contract suite passes for Fake + Local | `pytest tests/unit/store -q` |
| `ResearchStore` delegation test passes | same |
| Live Supabase round-trip | `pytest tests/integration -m live` |
| `research()` persists article to bucket | e2e + Supabase dashboard shows object |
| `make check` | green |

## Rollback

Set `artifact_store_kind="local"` (default); delete `supabase_artifact_store.py`.
No data migration involved — additive only.

## Non-goals

- Migrating `metadata.json`/`events.jsonl` to Storage (separate ADR if needed).
- Supabase Auth/Realtime/Edge; self-hosted Postgres.
- Async port (documented trade-off).

## Delivery plan (issue checklist)

Each task ends with `make check` green and its own commit (`store: …`).

- [ ] **T1 — Port + Local adapter** (`core/ports.py`, `store/artifact_store.py`)
  - [ ] T1.1 Contract suite (`test_artifact_store_contract.py`) parametrized
        over `Fake` + `Local` (RED first)
  - [ ] T1.2 `FakeArtifactStore` in `conftest.py`
  - [ ] T1.3 `ArtifactStorePort` + `LocalArtifactStore` (GREEN)
  - [ ] T1.4 Export from `store/__init__.py`
- [ ] **T2 — Delegate** (`store/research_store.py`)
  - [ ] T2.1 Spy test proving delegation + backward-compatible local copy
  - [ ] T2.2 Ctor takes `artifacts: ArtifactStorePort | None = None`; default Local
  - [ ] T2.3 Record `artifact_url` in `metadata.json`
- [ ] **T3 — Supabase adapter** (`store/supabase_artifact_store.py`)
  - [ ] T3.1 `from_env()` using `SUPABASE_URL` + `SUPABASE_SECRET_KEY`
  - [ ] T3.2 upsert to buckets `articles`/`sources`; return public URL
  - [ ] T3.3 `@pytest.mark.live` round-trip test (skips without keys)
  - [ ] T3.4 mypy vendor override for `supabase.*`
- [ ] **T4 — Composition root** (`engine.py`)
  - [ ] T4.1 `artifact_store_kind: str = "local"`; `"supabase"` builds adapter
  - [ ] T4.2 Typed `ConfigurationError` when keys missing
- [ ] **T5 — Close-out**
  - [ ] T5.1 e2e: `research()` persists and re-reads the article
  - [ ] T5.2 `SPEC.md` Phase 1 marked done; README note

## Test matrix

| Level | File | Marker | Runs in CI | Proves |
|-------|------|--------|-----------|--------|
| Unit | `tests/unit/store/test_artifact_store_contract.py` | — | always | LSP: every adapter substitutable |
| Unit | `tests/unit/store/test_research_store_delegates.py` | — | always | DIP: store depends on the port |
| Integration | `tests/integration/test_supabase_artifact_store.py` | `live` | opt-in (keys) | real Storage round-trip |
| E2E | `tests/e2e/test_engine_supabase.py` | `live` | opt-in (keys) | full run → durable artifact |

## Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | `supabase-py` sync blocks the event loop in async nodes | Med | Med | Adapter is sync by design; wrap in `asyncio.to_thread` at the call site if latency observed (no port change) |
| R2 | Free Storage cap (1 GB) exceeded | Low | Med | Articles are ~10–50 KB; add size guard + log; revisit at 100 runs |
| R3 | Contract drift — Local and Supabase diverge | Med | High | Single parametrized contract suite; `make check` blocks both |
| R4 | Secrets leak into image/logs | Low | High | Keys only via env; `.dockerignore`; never log DSNs (AGENTS rule 1) |
| R5 | `tach` rejects the new import edge | Low | Low | `store → core/schemas` already declared (tach.toml:26); external SDKs unconstrained |
| R6 | Evals snapshots shift | Low | Med | Default stays Local (byte-identical); Supabase path is opt-in only |

## References

- `core/ports.py` — existing port contracts (ISP/DIP precedent)
- `store/research_store.py:144,154` — methods being delegated
- `orchestration/store_factory.py:43` — postgres store precedent (pattern to mirror)
- `SPEC.md` §3 — Phase 1 acceptance criteria
- Hexagonal layering: `tach.toml:26-36`

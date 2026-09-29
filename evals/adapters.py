"""Wiring the real research engine into the eval suite.

To make `make evals` test the actual LangGraph research workflow (not just the
`_demo` self-test), point an eval case's `entrypoint` here and give the engine's
model calls a way to route through the cassette.

Two facts drive how you wire it:

1. The engine synthesizes through `infrastructure/providers.py`, which calls
   `litellm.acompletion`. For OFFLINE replay (the free, deterministic `make evals`
   on every PR) to work, every model call must go through `Cassette.chat`, exactly
   like the judge does. That means the provider has to accept an injectable
   client/cassette. That is the one real code change required — nothing else.

2. Until that interception exists, this adapter can only run in RECORD mode
   (`make evals-live`), which costs tokens and needs free-tier keys. It is not
   used by the default offline suite. The `_demo` case stays the CI-green check.

Recommended shape once the provider is cassette-aware:

    def research_run(query, max_sources=8, cassette=None, **_):
        from knowledge_engine.engine import ResearchEngine

        async def go():
            engine = ResearchEngine(store_root="research_store")
            # Route provider LLM calls through cassette.chat here (see providers.py).
            engine.provider.client = cassette.chat if cassette else None
            result = await engine.research(query, workflow="research")
            s = result.state
            return {
                "artifact": _read(s.output_path),   # article.md
                "sources": s.sources,
                "events": [],
            }

        return asyncio.run(go())

Then copy evals/cases/example.yaml.template to evals/cases/research.yaml with
`entrypoint: "evals.adapters:research_run"` and real invariants/budget. Use
`make evals-live` to record, review the cassette, `make evals-accept` for the
snapshot, and verify the case can fail before trusting it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


def _read(path: str | Path | None) -> str:
    if not path:
        return ""
    p = Path(path)
    return p.read_text() if p.exists() else ""


def research_run(
    query: str, max_sources: int = 8, cassette: Any | None = None, **_: Any
) -> dict[str, Any]:
    """Adapter contract: (**input, cassette=) -> {artifact, sources, events}.

    NOTE: currently only suitable for RECORD/live mode. Offline replay requires
    the engine's provider to route model calls through `cassette.chat` (see the
    module docstring). Keep using the `_demo` case for the offline CI gate until
    that interception is wired.
    """
    from knowledge_engine.engine import ResearchEngine

    async def go() -> dict[str, Any]:
        engine = ResearchEngine(store_root="research_store", search_limit=max_sources)
        # TODO: thread cassette into engine's provider so replay works offline.
        result = await engine.research(query, workflow="research")
        s = result.state
        return {
            "artifact": _read(s.output_path),
            "sources": s.sources,
            "events": [],
        }

    return asyncio.run(go())

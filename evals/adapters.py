"""Wiring the real research engine into the eval suite.

Runs the actual LangGraph research workflow (ingest → relevance → bodies →
synthesize → judge → review → render) with every network boundary recorded:

* provider LLM calls route through ``cassette.achat`` (see the ``cassette``
  hook on ``LiteLLMProviderAdapter``),
* federated search + scrape route through ``cassette.acall`` with
  Pydantic encode/decode.

So ``make evals`` (replay) is fully offline and deterministic, while
``make evals-live`` re-records against live APIs. ``extract()`` (Instructor
path, MCP toolkit only) is out of scope — the research graph uses
``complete()`` exclusively.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


def _read(path: str | Path | None) -> str:
    """Read an artifact file, tolerating a missing path.

    Args:
        path: Artifact path.

    Returns:
        str: File text, or empty string when absent.
    """
    if not path:
        return ""
    p = Path(path)
    return p.read_text() if p.exists() else ""


class _RecordingSearch:
    """SearchPort proxy recording through a cassette.

    Args:
        inner: Real search adapter.
        cassette: Eval cassette.
    """

    def __init__(self, inner: Any, cassette: Any) -> None:
        self.inner = inner
        self.cassette = cassette
        self.name = getattr(inner, "name", "search")

    async def search(self, query: str, *, limit: int = 10) -> Any:
        """Recorded federated search.

        Args:
            query: Scholarly query string.
            limit: Maximum records to return.

        Returns:
            Any: Resolved scholarly sources.
        """
        from knowledge_engine.schemas.source import SourceRecord

        return await self.cassette.acall(
            f"search:{self.name}",
            self.inner.search,
            {"query": query, "limit": limit},
            encode=lambda records: [r.model_dump(mode="json") for r in records],
            decode=lambda data: [SourceRecord.model_validate(d) for d in data],
        )


class _RecordingScrape:
    """ScrapePort proxy recording through a cassette.

    Args:
        inner: Real scrape adapter.
        cassette: Eval cassette.
    """

    def __init__(self, inner: Any, cassette: Any) -> None:
        self.inner = inner
        self.cassette = cassette

    async def to_markdown(self, url: str) -> Any:
        """Recorded URL fetch.

        Args:
            url: Target URL.

        Returns:
            Any: Markdown body.
        """
        from knowledge_engine.schemas.source import DocumentBody

        return await self.cassette.acall(
            "scrape",
            self.inner.to_markdown,
            {"url": url},
            encode=lambda body: body.model_dump(mode="json"),
            decode=DocumentBody.model_validate,
        )


def research_run(query: str, max_sources: int = 8, cassette: Any | None = None, **_: Any) -> dict[str, Any]:
    """Run the real research workflow with cassette-routed network calls.

    Uses a FIXED thread id and a dedicated store root, wiped on every run:
    replay must reproduce the exact artifact (judge cassette keys hash the
    full text, so any per-run identifier breaks replay). Single sample only.

    Args:
        query: Scholarly query string.
        max_sources: Maximum sources.
        cassette: Eval cassette (record/replay); ``None`` runs fully live.
        **_: Ignored extra inputs.

    Returns:
        dict[str, Any]: Artifact, sources, and events for the harness.
    """
    import shutil

    from knowledge_engine.engine import ResearchEngine

    root = Path("research_store/evals")
    shutil.rmtree(root / "eval-research", ignore_errors=True)
    shutil.rmtree(root / "checkpoints" / "eval-research", ignore_errors=True)

    async def go() -> dict[str, Any]:
        engine = ResearchEngine(store_root=str(root), search_limit=max_sources)
        if cassette is not None:
            provider = getattr(engine, "_provider", None)
            for adapter in getattr(provider, "adapters", {}).values():
                adapter.cassette = cassette
            # Eval-glue privilege: reach into the engine to route network
            # calls through the cassette. Production code uses the facade.
            engine._search = _RecordingSearch(engine._search, cassette)
            engine._scrape = _RecordingScrape(engine._scrape, cassette)
        result = await engine.research(query, workflow="research", thread_id="eval-research")
        s = result.state
        return {
            "artifact": _read(s.output_path),
            "sources": s.sources,
            "events": [],
            "bodies": len(s.bodies or []),
        }

    return asyncio.run(go())


def deep_run(query: str, max_sources: int = 8, cassette: Any | None = None, **_: Any) -> dict[str, Any]:
    """Run the recursive deep workflow with cassette-routed network calls.

    Fixed thread id + dedicated store root (same replay-stability contract as
    :func:`research_run`): judge cassette keys hash the full artifact text.

    Args:
        query: Scholarly query string.
        max_sources: Maximum sources.
        cassette: Eval cassette (record/replay); ``None`` runs fully live.
        **_: Ignored extra inputs.

    Returns:
        dict[str, Any]: Artifact, sources, events, and deep-loop metadata.
    """
    import shutil

    from knowledge_engine.engine import ResearchEngine

    root = Path("research_store/evals")
    shutil.rmtree(root / "eval-deep", ignore_errors=True)
    shutil.rmtree(root / "checkpoints" / "eval-deep", ignore_errors=True)

    async def go() -> dict[str, Any]:
        engine = ResearchEngine(store_root=str(root), search_limit=max_sources)
        if cassette is not None:
            provider = getattr(engine, "_provider", None)
            for adapter in getattr(provider, "adapters", {}).values():
                adapter.cassette = cassette
            engine._search = _RecordingSearch(engine._search, cassette)
            engine._scrape = _RecordingScrape(engine._scrape, cassette)
        result = await engine.research(query, workflow="deep", thread_id="eval-deep")
        s = result.state
        return {
            "artifact": _read(s.output_path),
            "sources": s.sources,
            "events": [],
            "bodies": len(s.bodies or []),
        }

    return asyncio.run(go())

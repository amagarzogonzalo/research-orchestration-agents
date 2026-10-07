"""Offline, deterministic performance bench (pre-flight baseline).

Runs the research + deep workflows end-to-end with in-module canned fakes —
no network, no LLM quota — and records per workflow:

* ``ram_kb``: peak allocated memory around the full run (``tracemalloc``),
* ``checkpoint_bytes``: durable checkpoint JSON under the run's store root,
* ``supersteps``: state-history length (checkpoint count),
* ``wall_s``: report-only wall time (excluded from regression checks).

Commands (offline, free)::

    uv run python -m knowledge_engine.scripts.bench            # print metrics
    uv run python -m knowledge_engine.scripts.bench --accept   # write baseline
    uv run python -m knowledge_engine.scripts.bench --check    # gate vs baseline

``--check`` fails on >10% growth of RAM or checkpoint bytes and on any
superstep growth. Determinism across invocations is asserted in
``tests/unit/test_bench.py`` for the pure-logic metrics (checkpoint bytes,
supersteps); peak RAM is environment-dependent (interpreter, allocator, GC
timing) and is guarded against the committed baseline only, never across
machines.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from knowledge_engine.core.ports import ProviderPort, ScrapePort, SearchPort
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import ChatRequest, ChatResult, UsageSummary
from knowledge_engine.schemas.source import DocumentBody, SearchSource, SourceRecord

BASELINE_PATH = Path("bench/baseline.json")
_TOLERANCE = 0.10
_WORKFLOWS = ("research", "deep")
_GATED_KEYS = ("ram_kb", "checkpoint_bytes", "supersteps")


@dataclass(frozen=True)
class _BenchSearch(SearchPort):
    """Deterministic two-record federated search (no network)."""

    async def search(self, query: str, *, limit: int = 10) -> list[SourceRecord]:
        """Return two fixed records.

        Args:
            query: Scholarly query string (ignored).
            limit: Maximum records (ignored).

        Returns:
            list[SourceRecord]: Two fixed openalex records.
        """
        return [
            SourceRecord(id="openalex:W1", title="Paper One", source=SearchSource.OPENALEX, abstract="a" * 400),
            SourceRecord(id="openalex:W2", title="Paper Two", source=SearchSource.OPENALEX, abstract="b" * 400),
        ]


@dataclass(frozen=True)
class _BenchScrape(ScrapePort):
    """Deterministic body fetch (no network)."""

    async def to_markdown(self, url: str) -> DocumentBody:
        """Return a fixed-size body.

        Args:
            url: Target URL.

        Returns:
            DocumentBody: Markdown body with deterministic content.
        """
        markdown = f"# {url}\n" + "x" * 2000
        return DocumentBody(url=url, title="Body", markdown=markdown, char_count=len(markdown))


@dataclass(frozen=True)
class _BenchProvider(ProviderPort):
    """Deterministic provider: canned replies keyed by the calling node's system prompt."""

    async def complete(self, req: ChatRequest) -> ChatResult:
        """Reply with a canned, node-appropriate payload.

        Args:
            req: Chat request (the system prompt selects the canned reply).

        Returns:
            ChatResult: Fixed reply + fixed usage.
        """
        system = req.messages[0].content if req.messages else ""
        if "librarian" in system:
            text = '{"relevant_ids": ["openalex:W1", "openalex:W2"]}'
        elif "research planner" in system:
            text = '{"sub_queries": [{"query": "sub a", "source": "search", "rationale": "r"}]}'
        elif "groundedness" in system:
            text = '{"score": 0.9, "pass": true, "critique": ""}'
        elif "research critic" in system:
            text = (
                '{"verdict": "sufficient", "coverage": 0.9, "diversity": 0.5, "grounding": 0.9,'
                ' "contradictions": [], "unanswered": [], "revision_guidance": "ok"}'
            )
        else:
            text = "## Findings\nGrounded synthesis note."
        usage = UsageSummary(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=0.0)
        return ChatResult.from_response(text, provider="bench", model="bench", usage=usage)

    async def extract(self, req: ExtractionRequest) -> object:
        """Return a stub extraction (unused by these workflows).

        Args:
            req: Extraction request.

        Returns:
            object: Empty payload.
        """
        return {}


async def _measure(workflow: str, root: Path) -> dict[str, int | float]:
    """Run one workflow end-to-end and collect its metrics.

    Args:
        workflow: ``research`` or ``deep``.
        root: Scratch directory for the store + checkpoints.

    Returns:
        dict[str, int | float]: ram_kb, checkpoint_bytes, supersteps, wall_s.
    """
    thread = f"bench-{workflow}"
    tracemalloc.start()
    start = time.perf_counter()
    try:
        engine = ResearchEngine(
            store_root=str(root / "store"),
            checkpointer_kind="file",
            store_kind="memory",
            search=_BenchSearch(),
            scrape=_BenchScrape(),
            provider=_BenchProvider(),
        )
        result = await engine.research(
            "state of the art in retrieval augmented generation",
            workflow=workflow,
            thread_id=thread,
        )
        assert result.state.output_path, "bench run must render an artifact"
        graph = engine.compiled_graph(workflow)
        history = [s async for s in graph.aget_state_history({"configurable": {"thread_id": thread}})]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    wall = time.perf_counter() - start
    checkpoints = root / "store" / "checkpoints"
    checkpoint_bytes = sum(p.stat().st_size for p in checkpoints.rglob("*.json"))
    return {
        "ram_kb": peak // 1024,
        "checkpoint_bytes": checkpoint_bytes,
        "supersteps": len(history),
        "wall_s": round(wall, 2),
    }


def collect() -> dict[str, dict[str, int | float]]:
    """Measure every workflow in a fresh scratch directory.

    A discarded warmup run absorbs one-time lazy imports; ``gc.collect()``
    before each measurement clears the previous run's reference cycles, so
    two collections in one process produce identical numbers (asserted in
    ``tests/unit/test_bench.py``).

    Returns:
        dict[str, dict[str, int | float]]: workflow name -> metrics mapping.
    """

    async def _all() -> dict[str, dict[str, int | float]]:
        with tempfile.TemporaryDirectory() as scratch:
            await _measure(_WORKFLOWS[0], Path(scratch))  # warmup: lazy imports, gc state
        out: dict[str, dict[str, int | float]] = {}
        for workflow in _WORKFLOWS:
            gc.collect()
            with tempfile.TemporaryDirectory() as scratch:
                out[workflow] = await _measure(workflow, Path(scratch))
        return out

    gc.collect()
    return asyncio.run(_all())


def check(
    current: dict[str, dict[str, int | float]],
    baseline: dict[str, dict[str, int | float]],
) -> list[str]:
    """Compare fresh metrics against the committed baseline.

    Args:
        current: Freshly collected metrics.
        baseline: Committed baseline mapping.

    Returns:
        list[str]: Human-readable regressions (empty when green).
    """
    failures: list[str] = []
    for workflow, metrics in current.items():
        base = baseline.get(workflow, {})
        for key in _GATED_KEYS:
            base_value = base.get(key)
            if base_value is None:
                failures.append(f"{workflow}.{key}: no baseline value")
                continue
            if metrics[key] > base_value * (1 + _TOLERANCE):
                failures.append(f"{workflow}.{key}: {metrics[key]} > {base_value} (+{_TOLERANCE:.0%})")
    return failures


def main(argv: list[str] | None = None) -> int:
    """CLI entry: collect, print, and optionally accept or check a baseline.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 green, 1 regression, 2 missing baseline.
    """
    parser = argparse.ArgumentParser(description="offline perf bench for the research workflows")
    parser.add_argument("--accept", action="store_true", help="write bench/baseline.json (review like code)")
    parser.add_argument("--check", action="store_true", help="fail on >10% ram/checkpoint regression")
    parser.add_argument("--json", action="store_true", help="print metrics as JSON")
    args = parser.parse_args(argv)

    current = collect()
    if args.json:
        print(json.dumps(current, indent=2, sort_keys=True))
    for workflow, metrics in sorted(current.items()):
        print(
            f"{workflow}: ram={metrics['ram_kb']}kB ckpt={metrics['checkpoint_bytes']}B "
            f"supersteps={metrics['supersteps']} wall={metrics['wall_s']}s"
        )

    if args.accept:
        BASELINE_PATH.parent.mkdir(exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"baseline written: {BASELINE_PATH} (commit it with this change)")
        return 0

    if args.check:
        if not BASELINE_PATH.exists():
            print(f"no baseline at {BASELINE_PATH}; run --accept first")
            return 2
        baseline = cast(dict[str, dict[str, int | float]], json.loads(BASELINE_PATH.read_text()))
        failures = check(current, baseline)
        for failure in failures:
            print(f"REGRESSION {failure}")
        print("bench green" if not failures else "bench regressed")
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

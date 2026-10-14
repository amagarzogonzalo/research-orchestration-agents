"""Suite W — research and deep-research workflow coverage.

Exercises the two headline use cases end to end on the deterministic fakes
from :mod:`knowledge_engine.scripts.bench` (no network, no LLM quota): the
``research`` workflow and the recursive ``deep`` workflow. Reported metrics are
the deterministic superstep counts plus an artifact-produced flag, so the wiki
benchmark stays anchored to the use cases it exists to serve.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from knowledge_engine.scripts.bench import collect
from knowledge_engine.scripts.wikibench.schemas import SuiteResult


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite W and return the research/deep workflow metrics.

    Args:
        workspace: Scratch directory (bench uses its own temp dirs).
        scale: Corpus size (unused; suite W runs the workflows).
        seed: Corpus seed (unused).
        backend: Backend id.

    Returns:
        SuiteResult: Workflow metrics and threshold findings.
    """
    data = await asyncio.to_thread(collect)
    research = data.get("research", {})
    deep = data.get("deep", {})
    deep_artifact_ok = 1.0 if deep.get("supersteps", 0) > 0 and deep.get("checkpoint_bytes", 0) > 0 else 0.0
    measured = {
        "research_supersteps": float(research.get("supersteps", 0)),
        "deep_supersteps": float(deep.get("supersteps", 0)),
        "deep_artifact_ok": deep_artifact_ok,
        "research_wall_s": float(research.get("wall_s", 0.0)),
        "deep_wall_s": float(deep.get("wall_s", 0.0)),
    }
    findings: list[str] = []
    if measured["research_supersteps"] <= 0.0 or measured["deep_supersteps"] <= 0.0:
        findings.append("a research/deep workflow did not complete")
    if deep_artifact_ok < 1.0:
        findings.append("deep workflow produced no artifact")
    return SuiteResult(
        name="w_workflows",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

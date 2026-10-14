"""Use case: run research and deep research.

Suite ``workflows`` exercises the two headline engine use cases end to end on
the deterministic fakes from :mod:`knowledge_engine.scripts.bench` (no network,
no LLM quota), reporting the deterministic superstep counts plus an
artifact-produced flag.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from knowledge_engine.scripts.bench import collect
from knowledge_engine.wikibench.schemas import SuiteResult

_USE_CASE = "run-research"


async def workflows(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run the research/deep workflow suite.

    Args:
        workspace: Scratch directory (the bench uses its own temp dirs).
        scale: Corpus size (unused; the suite runs the workflows).
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
        name="workflows",
        use_case=_USE_CASE,
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["workflows"]

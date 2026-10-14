"""Use case: run research, QA, summary and deep research.

Suite ``workflows`` exercises every workflow the engine exposes — ``research``,
``qa``, ``summary`` and ``deep`` — end to end on the deterministic fakes from
:mod:`knowledge_engine.scripts.bench` (no network, no LLM quota), reporting the
superstep count and an artifact-produced flag per workflow.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.scripts.bench import measure_workflow
from knowledge_engine.wikibench.schemas import SuiteResult

_USE_CASE = "run-research"
_WORKFLOWS = ("research", "qa", "summary", "deep")


async def workflows(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run every workflow suite and report per-workflow metrics.

    Args:
        workspace: Scratch directory (each workflow gets ``workspace/<name>``).
        scale: Corpus size (unused; the suite runs the workflows).
        seed: Corpus seed (unused).
        backend: Backend id.

    Returns:
        SuiteResult: Per-workflow metrics and threshold findings.
    """
    measured: dict[str, float] = {}
    findings: list[str] = []
    for workflow in _WORKFLOWS:
        try:
            data = await measure_workflow(workflow, workspace / workflow)
        except Exception as exc:  # noqa: BLE001 - report which workflow failed
            findings.append(f"{workflow} workflow failed: {type(exc).__name__}: {exc}")
            continue
        supersteps = float(data.get("supersteps", 0))
        checkpoint_bytes = float(data.get("checkpoint_bytes", 0))
        measured[f"{workflow}_supersteps"] = supersteps
        measured[f"{workflow}_ok"] = 1.0 if supersteps > 0 and checkpoint_bytes > 0 else 0.0
        measured[f"{workflow}_wall_s"] = float(data.get("wall_s", 0.0))
        if measured[f"{workflow}_ok"] < 1.0:
            findings.append(f"{workflow} workflow produced no artifact/checkpoint")
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

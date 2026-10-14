"""WikiBench suite registry (deterministic Tier 0).

Each suite is an ``async def run(*, workspace, scale, seed, backend)`` coroutine
returning a :class:`SuiteResult`. Suites are pure with respect to the outside
world: file backend only, no network, no LLM.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from knowledge_engine.scripts.wikibench.schemas import SuiteResult
from knowledge_engine.scripts.wikibench.suites import (
    a_retrieval,
    b_miss,
    c_compounding,
    d_ingest,
    i_lint,
    j_robustness,
    w_workflows,
)

SuiteFn = Callable[..., Awaitable[SuiteResult]]

#: Suite name -> coroutine, in headline order.
SUITES: dict[str, SuiteFn] = {
    "a_retrieval": a_retrieval.run,
    "b_miss": b_miss.run,
    "c_compounding": c_compounding.run,
    "d_ingest": d_ingest.run,
    "i_lint": i_lint.run,
    "j_robustness": j_robustness.run,
    "w_workflows": w_workflows.run,
}


async def run_suite(name: str, *, workspace: Path, scale: int, seed: int, backend: str = "file") -> SuiteResult:
    """Run one named suite.

    Args:
        name: Suite name (a key of :data:`SUITES`).
        workspace: Scratch directory for the suite.
        scale: Corpus size.
        seed: Corpus seed.
        backend: Backend id.

    Returns:
        SuiteResult: The suite outcome.

    Raises:
        KeyError: When the suite name is unknown.
    """
    if name not in SUITES:
        raise KeyError(name)
    return await SUITES[name](workspace=workspace, scale=scale, seed=seed, backend=backend)


__all__ = ["SUITES", "SuiteFn", "run_suite"]

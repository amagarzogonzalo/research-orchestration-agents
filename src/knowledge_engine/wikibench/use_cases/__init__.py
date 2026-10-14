"""Use-case suites for WikiBench, grouped by the question a user asks.

Each module is one use case and exposes one or more
``async def suite(*, workspace, scale, seed, backend) -> SuiteResult``
coroutines. :data:`USE_CASES` groups the suites; :data:`SUITES` is the flat
registry the runner drives.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from knowledge_engine.wikibench.schemas import SuiteResult
from knowledge_engine.wikibench.use_cases import (
    answering,
    compounding,
    ingestion,
    maintenance,
    provenance,
    research,
)

SuiteFn = Callable[..., Awaitable[SuiteResult]]

#: Flat suite registry: suite name -> coroutine.
SUITES: dict[str, SuiteFn] = {
    "retrieval": answering.retrieval,
    "miss": answering.miss,
    "cost": compounding.cost,
    "ingest": ingestion.ingest,
    "lint": maintenance.lint,
    "robustness": maintenance.robustness,
    "provenance": provenance.provenance,
    "workflows": research.workflows,
}

#: Use case -> the suites that answer it.
USE_CASES: dict[str, tuple[str, ...]] = {
    "answer-from-wiki": ("retrieval", "miss"),
    "compound-knowledge": ("cost",),
    "file-research": ("ingest",),
    "maintain-wiki": ("lint", "robustness", "provenance"),
    "run-research": ("workflows",),
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


__all__ = ["SUITES", "USE_CASES", "SuiteFn", "run_suite"]

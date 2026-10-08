"""Derived run limits — recursion budgets computed from shared constants (ADR 0014).

``recursion_limit`` is the graph's hard loop bound; the guardrails
(:mod:`knowledge_engine.orchestration.guardrails`) are the soft bounds honored
in normal runs. The limit is derived from the same budget constants the nodes
enforce, so a budget change visibly changes the limit and no magic number
drifts apart.

Pregel accounting (verified against the compiled topologies): ``Send``
fan-out tasks share one superstep regardless of branch count, and node
retries re-attempt within their superstep — so limits do not scale with
branch count or retry count.
"""

from __future__ import annotations

from knowledge_engine.schemas.decomposition import MAX_DEEP_ROUNDS
from knowledge_engine.schemas.extraction import MAX_SYNTHESIS_ITERATIONS

#: Supersteps consumed by one deep replan round: decompose, branch fan-out
#: (all Sends share one superstep), aggregate, critic, judge, synthesize.
_ROUND_STEPS = 6

#: Review + render after the loop, plus slack for error-handler hops.
_TAIL_STEPS = 2


def derive_recursion_limit(workflow: str, *, safety: int = 3) -> int:
    """Return the recursion limit for a workflow, derived from budget constants.

    Args:
        workflow: One of ``research``, ``qa``, ``summary``, ``deep``.
        safety: Multiplier over the worst-case superstep count.

    Returns:
        int: Recursion limit exceeding the worst case.

    Raises:
        ValueError: When the workflow name is unknown.
    """
    if workflow == "deep":
        # START/intent + replan rounds (decompose, branch fan-out, aggregate,
        # critic, judge, synthesize) + review/render tail.
        steps = 1 + MAX_DEEP_ROUNDS * _ROUND_STEPS + _TAIL_STEPS
    elif workflow == "research":
        # ingest, relevance, bodies route, fetch fan-out, then the bounded
        # synthesize+judge optimizer loop, then review + render.
        steps = 4 + 2 * MAX_SYNTHESIS_ITERATIONS + _TAIL_STEPS
    elif workflow in ("qa", "summary"):
        steps = 1 + 4 + _TAIL_STEPS
    else:
        raise ValueError(f"unknown workflow: {workflow!r}")
    return (steps + 1) * safety


DEFAULT_LIMITS: dict[str, int] = {
    workflow: derive_recursion_limit(workflow) for workflow in ("research", "qa", "summary", "deep")
}

__all__ = ["DEFAULT_LIMITS", "derive_recursion_limit"]

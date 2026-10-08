"""Derived recursion limits (ADR 0014): derivation linkage + runtime plumbing.

The limit is the graph's hard loop bound — it must be *derived* from the same
budget constants the nodes enforce, never hand-copied, and it must dominate
any reachable superstep count.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

import knowledge_engine.orchestration.limits as limits


def test_default_limits_equal_derivation() -> None:
    """DEFAULT_LIMITS is computed from the same function (no hand-copied magic)."""
    assert limits.DEFAULT_LIMITS == {
        workflow: limits.derive_recursion_limit(workflow) for workflow in ("research", "qa", "summary", "deep")
    }


def test_deep_limit_tracks_round_budget(monkeypatch) -> None:
    """Raising MAX_DEEP_ROUNDS must raise the derived limit."""
    monkeypatch.setattr(limits, "MAX_DEEP_ROUNDS", limits.MAX_DEEP_ROUNDS + 2)
    assert limits.derive_recursion_limit("deep") > limits.DEFAULT_LIMITS["deep"]


def test_unknown_workflow_rejected() -> None:
    """Unknown workflow names fail loudly, never silently default."""
    with pytest.raises(ValueError):
        limits.derive_recursion_limit("nonsense")


@given(safety=st.integers(1, 6), rounds=st.integers(1, 8))
def test_limit_dominates_worst_case(safety: int, rounds: int) -> None:
    """For any safety >= 1 and rounds budget, the limit exceeds the worst case."""
    original = limits.MAX_DEEP_ROUNDS
    limits.MAX_DEEP_ROUNDS = rounds
    try:
        limit = limits.derive_recursion_limit("deep", safety=safety)
    finally:
        limits.MAX_DEEP_ROUNDS = original
    worst = 1 + rounds * 6 + 2  # mirrors _ROUND_STEPS/_TAIL_STEPS; drift guard
    assert limit >= worst + 1

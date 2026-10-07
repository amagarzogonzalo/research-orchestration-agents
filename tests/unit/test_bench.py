"""Bench determinism + regression-gate logic (pre-flight safety net).

The bench is the measurement instrument every later refactor is judged with,
so its own determinism is asserted first: identical canned fakes must produce
identical checkpoint bytes and superstep counts, and stable RAM within jitter.
"""

from __future__ import annotations

from knowledge_engine.scripts.bench import check, collect


def test_collect_is_deterministic() -> None:
    """Two collections agree exactly on supersteps/checkpoint bytes, ~5% on RAM."""
    first, second = collect(), collect()
    for workflow in ("research", "deep"):
        assert first[workflow]["supersteps"] == second[workflow]["supersteps"], workflow
        assert first[workflow]["checkpoint_bytes"] == second[workflow]["checkpoint_bytes"], workflow
        ram_first, ram_second = int(first[workflow]["ram_kb"]), int(second[workflow]["ram_kb"])
        jitter = max(1, int(0.05 * max(ram_first, ram_second)))
        assert abs(ram_first - ram_second) <= jitter, workflow


def test_check_passes_on_identical_metrics() -> None:
    """An unchanged system must not regress against its own baseline."""
    baseline = collect()
    assert check(baseline, baseline) == []


def test_check_flags_growth() -> None:
    """RAM growth beyond tolerance must be flagged, other keys stay silent."""
    baseline = collect()
    current = {workflow: dict(metrics) for workflow, metrics in baseline.items()}
    current["research"]["ram_kb"] = int(baseline["research"]["ram_kb"] * 2) + 1
    failures = check(current, baseline)
    assert any("research.ram_kb" in failure for failure in failures)
    assert all("deep" not in failure for failure in failures)

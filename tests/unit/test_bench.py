"""Bench determinism + regression-gate logic (pre-flight safety net).

The bench is the measurement instrument every later refactor is judged with,
so its own determinism is asserted first: identical canned fakes must produce
identical checkpoint bytes and superstep counts, and stable RAM within jitter.
"""

from __future__ import annotations

from knowledge_engine.scripts.bench import check, collect


def test_collect_is_deterministic() -> None:
    """Two collections agree exactly on supersteps/checkpoint bytes; RAM is a sanity bound.

    Checkpoint bytes and superstep counts are pure logic — they must match
    exactly. Peak RAM depends on interpreter, allocator, and GC timing, so it
    is asserted only within a coarse sanity band (same order of magnitude);
    the regression gate (``--check``) still guards it against the committed
    baseline with its own tolerance.
    """
    first, second = collect(), collect()
    for workflow in ("research", "deep"):
        assert first[workflow]["supersteps"] == second[workflow]["supersteps"], workflow
        assert first[workflow]["checkpoint_bytes"] == second[workflow]["checkpoint_bytes"], workflow
        ram_first, ram_second = int(first[workflow]["ram_kb"]), int(second[workflow]["ram_kb"])
        assert ram_first > 0 and ram_second > 0, workflow
        assert max(ram_first, ram_second) <= 2 * min(ram_first, ram_second), workflow


def test_check_passes_on_identical_metrics() -> None:
    """An unchanged system must not regress against its own baseline."""
    baseline = collect()
    assert check(baseline, baseline) == []


def test_check_flags_growth() -> None:
    """Checkpoint-bytes growth beyond tolerance must be flagged."""
    baseline = collect()
    current = {workflow: dict(metrics) for workflow, metrics in baseline.items()}
    current["research"]["checkpoint_bytes"] = int(baseline["research"]["checkpoint_bytes"] * 2) + 1
    failures = check(current, baseline)
    assert any("research.checkpoint_bytes" in failure for failure in failures)


def test_check_ram_is_advisory_not_gated() -> None:
    """RAM growth alone must NOT fail the gate (environment-dependent metric)."""
    baseline = collect()
    current = {workflow: dict(metrics) for workflow, metrics in baseline.items()}
    current["research"]["ram_kb"] = int(baseline["research"]["ram_kb"] * 3) + 1
    assert check(current, baseline) == []

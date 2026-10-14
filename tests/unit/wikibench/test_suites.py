"""Unit tests: WikiBench use-case suites (deterministic Tier 0, file backend)."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.wikibench.use_cases import run_suite


async def test_retrieval_recall_and_no_duplicates(tmp_path: Path) -> None:
    """The retrieval suite returns the right pages with no duplicate hits."""
    result = await run_suite("retrieval", workspace=tmp_path, scale=20, seed=7)
    assert result.passed
    assert result.use_case == "answer-from-wiki"
    assert result.metrics["recall@3"] >= 0.9
    assert result.metrics["dup_rate"] == 0.0


async def test_retrieval_hybrid_not_worse_than_scan(tmp_path: Path) -> None:
    """Hybrid (FTS+scan) recall is never below the scan baseline."""
    result = await run_suite("retrieval", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["hybrid_recall@3"] >= result.metrics["scan_recall@3"]


async def test_miss_stays_quiet(tmp_path: Path) -> None:
    """The miss suite stays quiet on unrelated queries."""
    result = await run_suite("miss", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["fp_rate"] <= 0.05
    assert result.metrics["miss_pass_rate"] == 1.0
    assert result.metrics["exact_classify_acc"] == 1.0


async def test_cost_compounds(tmp_path: Path) -> None:
    """The cost suite shows the zero-live-call rate rising over the stream."""
    result = await run_suite("cost", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["zero_call_lift"] > 0.0
    assert result.metrics["zero_call_rate_final"] > 0.0


async def test_ingest_verdicts_and_idempotency(tmp_path: Path) -> None:
    """The ingest suite applies verdicts correctly and replays byte-identically."""
    result = await run_suite("ingest", workspace=tmp_path, scale=10, seed=7)
    assert result.metrics["verdict_accuracy"] == 1.0
    assert result.metrics["idempotent_bytes_equal"] == 1.0
    assert result.metrics["index_drift"] == 0.0
    assert result.metrics["dup_rate"] == 0.0


async def test_lint_detects_seeded_defects(tmp_path: Path) -> None:
    """The lint suite finds every seeded defect and passes a clean vault."""
    result = await run_suite("lint", workspace=tmp_path, scale=10, seed=7)
    assert result.metrics["clean_ok"] == 1.0
    assert result.metrics["recall"] == 1.0


async def test_robustness_survives_hostile_input(tmp_path: Path) -> None:
    """The robustness suite keeps the index intact through hostile input."""
    result = await run_suite("robustness", workspace=tmp_path, scale=5, seed=7)
    assert result.metrics["injection_index_intact"] == 1.0
    assert result.metrics["huge_ok"] == 1.0
    assert result.metrics["unicode_ok"] == 1.0
    assert result.metrics["index_complete"] == 1.0


async def test_workflows_run_research_and_deep(tmp_path: Path) -> None:
    """The workflows suite exercises research and deep research end to end."""
    result = await run_suite("workflows", workspace=tmp_path, scale=1, seed=7)
    assert result.metrics["deep_artifact_ok"] == 1.0
    assert result.metrics["research_supersteps"] > 0
    assert result.metrics["deep_supersteps"] > 0


async def test_unknown_suite_raises(tmp_path: Path) -> None:
    """An unknown suite name raises a KeyError."""
    with pytest.raises(KeyError):
        await run_suite("nope", workspace=tmp_path, scale=1, seed=7)

"""Unit tests: WikiBench deterministic Tier 0 suites (file backend)."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.scripts.wikibench import suites


async def test_suite_a_recall_and_no_duplicates(tmp_path: Path) -> None:
    """Suite A returns the right pages with no duplicate hits."""
    result = await suites.run_suite("a_retrieval", workspace=tmp_path, scale=20, seed=7)
    assert result.passed
    assert result.metrics["recall@3"] >= 0.9
    assert result.metrics["dup_rate"] == 0.0


async def test_suite_a_hybrid_not_worse_than_scan(tmp_path: Path) -> None:
    """Hybrid (FTS+scan) recall is never below the scan baseline."""
    result = await suites.run_suite("a_retrieval", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["hybrid_recall@3"] >= result.metrics["scan_recall@3"]


async def test_suite_b_no_false_positives(tmp_path: Path) -> None:
    """Suite B stays quiet on unrelated queries."""
    result = await suites.run_suite("b_miss", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["fp_rate"] <= 0.05
    assert result.metrics["miss_pass_rate"] == 1.0


async def test_suite_b_exact_classification(tmp_path: Path) -> None:
    """Exact-hit vs scored-only classification is correct."""
    result = await suites.run_suite("b_miss", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["exact_classify_acc"] == 1.0


async def test_suite_c_compounds(tmp_path: Path) -> None:
    """Suite C shows the zero-live-call rate rising over the stream."""
    result = await suites.run_suite("c_compounding", workspace=tmp_path, scale=20, seed=7)
    assert result.metrics["zero_call_lift"] > 0.0
    assert result.metrics["zero_call_rate_final"] > 0.0


async def test_suite_d_verdicts_and_idempotency(tmp_path: Path) -> None:
    """Suite D applies verdicts correctly and replays byte-identically."""
    result = await suites.run_suite("d_ingest", workspace=tmp_path, scale=10, seed=7)
    assert result.metrics["verdict_accuracy"] == 1.0
    assert result.metrics["idempotent_bytes_equal"] == 1.0
    assert result.metrics["index_drift"] == 0.0
    assert result.metrics["dup_rate"] == 0.0


async def test_suite_i_detects_seeded_defects(tmp_path: Path) -> None:
    """Suite I finds every seeded defect class and passes a clean vault."""
    result = await suites.run_suite("i_lint", workspace=tmp_path, scale=10, seed=7)
    assert result.metrics["clean_ok"] == 1.0
    assert result.metrics["recall"] == 1.0


async def test_suite_j_survives_hostile_input(tmp_path: Path) -> None:
    """Suite J keeps the index intact through injection/huge/unicode input."""
    result = await suites.run_suite("j_robustness", workspace=tmp_path, scale=5, seed=7)
    assert result.metrics["injection_index_intact"] == 1.0
    assert result.metrics["huge_ok"] == 1.0
    assert result.metrics["unicode_ok"] == 1.0
    assert result.metrics["index_complete"] == 1.0


async def test_suite_w_runs_research_and_deep(tmp_path: Path) -> None:
    """Suite W exercises the research and deep workflows end to end."""
    result = await suites.run_suite("w_workflows", workspace=tmp_path, scale=1, seed=7)
    assert result.metrics["deep_artifact_ok"] == 1.0
    assert result.metrics["research_supersteps"] > 0
    assert result.metrics["deep_supersteps"] > 0


async def test_unknown_suite_raises(tmp_path: Path) -> None:
    """An unknown suite name raises a KeyError."""
    with pytest.raises(KeyError):
        await suites.run_suite("nope", workspace=tmp_path, scale=1, seed=7)

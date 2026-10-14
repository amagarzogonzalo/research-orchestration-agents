"""Unit tests: WikiBench runner (report determinism + threshold gate)."""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_engine.scripts.wikibench import runner
from knowledge_engine.scripts.wikibench.schemas import BenchReport, SuiteResult


async def test_collect_produces_report(tmp_path: Path) -> None:
    """collect runs the requested suites into a report."""
    report = await runner.collect(["a_retrieval", "d_ingest"], workspace=tmp_path, scale=10, seed=7)
    assert {s.name for s in report.suites} == {"a_retrieval", "d_ingest"}
    assert report.metrics()


async def test_report_is_deterministic(tmp_path: Path) -> None:
    """Two identical runs serialize to identical JSON (no timestamps)."""
    first = await runner.collect(["b_miss"], workspace=tmp_path / "one", scale=10, seed=7)
    second = await runner.collect(["b_miss"], workspace=tmp_path / "two", scale=10, seed=7)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_evaluate_flags_hard_threshold_violation() -> None:
    """A metric below a hard threshold is reported as a failure."""
    report = BenchReport(
        backend="file",
        tier="0",
        scale=10,
        seed=7,
        suites=[
            SuiteResult(
                name="a_retrieval",
                backend="file",
                tier="0",
                scale=10,
                metrics={"dup_rate": 0.5},
            )
        ],
    )
    failures = runner.evaluate(report, baseline=None)
    assert any("dup_rate" in f for f in failures)


def test_evaluate_flags_baseline_regression() -> None:
    """A recall drop below the committed baseline is a regression."""
    report = BenchReport(
        backend="file",
        tier="0",
        scale=10,
        seed=7,
        suites=[
            SuiteResult(
                name="a_retrieval",
                backend="file",
                tier="0",
                scale=10,
                metrics={"recall@3": 0.5},
            )
        ],
    )
    baseline = {"a_retrieval.recall@3": 0.95}
    failures = runner.evaluate(report, baseline=baseline)
    assert any("recall@3" in f for f in failures)


def test_evaluate_passes_clean_report() -> None:
    """A report meeting all thresholds and matching the baseline is green."""
    report = BenchReport(
        backend="file",
        tier="0",
        scale=10,
        seed=7,
        suites=[
            SuiteResult(
                name="a_retrieval",
                backend="file",
                tier="0",
                scale=10,
                metrics={"dup_rate": 0.0, "recall@3": 1.0},
            )
        ],
    )
    assert runner.evaluate(report, baseline={"a_retrieval.recall@3": 1.0}) == []


def test_main_accept_and_check(tmp_path: Path, monkeypatch) -> None:
    """--accept writes a baseline and a matching --check is green."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bench" / "wikibench").mkdir(parents=True)
    assert runner.main(["--suite", "b_miss", "--scale", "10", "--accept", "--workspace", str(tmp_path)]) == 0
    baseline_path = tmp_path / "bench" / "wikibench" / "baseline.json"
    assert baseline_path.is_file()
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert "b_miss.fp_rate" in payload
    assert runner.main(["--suite", "b_miss", "--scale", "10", "--check", "--workspace", str(tmp_path)]) == 0

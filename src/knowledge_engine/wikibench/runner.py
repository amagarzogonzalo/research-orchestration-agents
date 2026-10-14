"""WikiBench runner library: collect suites, evaluate, render and persist.

Pure library — no ``print`` (the CLI entrypoint under
``knowledge_engine.scripts.wikibench`` owns presentation). Callers:

* :func:`collect` runs the requested suites into a report.
* :func:`evaluate` checks a report against hard thresholds and the baseline.
* :func:`render_markdown`, :func:`save_baseline`, :func:`write_report` persist.
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_engine.wikibench import thresholds
from knowledge_engine.wikibench.schemas import BenchReport
from knowledge_engine.wikibench.use_cases import run_suite

BASELINE_PATH = Path("bench/wikibench/baseline.json")
REPORT_JSON_PATH = Path("bench/wikibench/report.json")
REPORT_MD_PATH = Path("bench/wikibench/report.md")
DEFAULT_SCALE = 10
DEFAULT_SEED = 7
DEFAULT_BACKEND = "file"
DEFAULT_TIER = "0"


def _satisfies(value: float, operator: str, threshold: float) -> bool:
    """Return whether a metric satisfies ``value <op> threshold``.

    Args:
        value: Measured metric value.
        operator: One of ``<=``, ``>=``, ``<``, ``>``.
        threshold: Threshold value.

    Returns:
        bool: True when the comparison holds.

    Raises:
        ValueError: On an unknown operator.
    """
    if operator == "<=":
        return value <= threshold + 1e-12
    if operator == ">=":
        return value >= threshold - 1e-12
    if operator == "<":
        return value < threshold
    if operator == ">":
        return value > threshold
    raise ValueError(f"unknown operator: {operator!r}")


async def collect(
    suites: list[str],
    *,
    workspace: Path,
    scale: int = DEFAULT_SCALE,
    seed: int = DEFAULT_SEED,
    backend: str = DEFAULT_BACKEND,
    tier: str = DEFAULT_TIER,
) -> BenchReport:
    """Run the named suites into a single report.

    Args:
        suites: Suite names to run (keys of ``SUITES``).
        workspace: Scratch root; each suite gets ``workspace/<name>``.
        scale: Corpus size.
        seed: Corpus seed.
        backend: Backend id.
        tier: Execution tier.

    Returns:
        BenchReport: The combined report.
    """
    results = [
        await run_suite(name, workspace=workspace / name, scale=scale, seed=seed, backend=backend) for name in suites
    ]
    return BenchReport(backend=backend, tier=tier, scale=scale, seed=seed, suites=results)


def evaluate(report: BenchReport, baseline: dict[str, float] | None) -> list[str]:
    """Check a report against hard thresholds and (optionally) the baseline.

    Args:
        report: The report to check.
        baseline: Flattened baseline metrics, or None to skip the regression gate.

    Returns:
        list[str]: Human-readable failures (empty when green).
    """
    metrics = report.metrics()
    failures: list[str] = []
    for key, (operator, threshold) in thresholds.THRESHOLDS.items():
        if key in metrics and not _satisfies(metrics[key], operator, threshold):
            failures.append(f"{key}: {metrics[key]:.4g} not {operator} {threshold:.4g}")
    if baseline:
        for key in thresholds.REGRESSION_METRICS:
            if key in metrics and key in baseline and metrics[key] + thresholds.REGRESSION_TOLERANCE < baseline[key]:
                failures.append(f"{key}: {metrics[key]:.4g} < baseline {baseline[key]:.4g}")
    return failures


def render_markdown(report: BenchReport) -> str:
    """Render a report as a per-suite markdown table.

    Args:
        report: The report to render.

    Returns:
        str: Markdown summary.
    """
    lines = [
        f"# WikiBench report ({report.backend}, tier {report.tier}, scale {report.scale}, seed {report.seed})",
        "",
        "| Use case | Suite | Metric | Value |",
        "|---|---|---|---|",
    ]
    for suite in report.suites:
        for metric, value in sorted(suite.metrics.items()):
            lines.append(f"| {suite.use_case} | {suite.name} | {metric} | {value:.4g} |")
    return "\n".join(lines) + "\n"


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float] | None:
    """Load flattened baseline metrics, or None when absent.

    Args:
        path: Baseline JSON path.

    Returns:
        dict[str, float] | None: Metrics mapping or None.
    """
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): float(value) for key, value in loaded.items()}


def save_baseline(report: BenchReport, path: Path = BASELINE_PATH) -> None:
    """Write the report's flattened metrics as the baseline.

    Args:
        report: The report whose metrics become the baseline.
        path: Destination path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.metrics(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report(report: BenchReport) -> tuple[Path, Path]:
    """Write the JSON and markdown report artifacts.

    Args:
        report: The report to persist.

    Returns:
        tuple[Path, Path]: The (json, markdown) paths written.
    """
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    REPORT_MD_PATH.write_text(render_markdown(report), encoding="utf-8")
    return REPORT_JSON_PATH, REPORT_MD_PATH


def self_test() -> bool:
    """Known-bad control: the gate must reject a degraded report and pass a clean one.

    Returns:
        bool: True when the gate passes the clean report and fails the degraded one.
    """
    from knowledge_engine.wikibench.schemas import BenchReport, SuiteResult

    clean = BenchReport(
        backend="file",
        tier="0",
        scale=1,
        seed=1,
        suites=[
            SuiteResult(
                name="retrieval",
                use_case="answer-from-wiki",
                backend="file",
                tier="0",
                scale=1,
                metrics={"dup_rate": 0.0, "recall@3": 1.0},
            )
        ],
    )
    degraded = clean.model_copy(deep=True)
    degraded.suites[0].metrics["recall@3"] = 0.1
    degraded.suites[0].metrics["dup_rate"] = 0.5
    return evaluate(clean, None) == [] and evaluate(degraded, None) != []


__all__ = [
    "BASELINE_PATH",
    "REPORT_JSON_PATH",
    "REPORT_MD_PATH",
    "collect",
    "evaluate",
    "load_baseline",
    "render_markdown",
    "save_baseline",
    "self_test",
    "write_report",
]

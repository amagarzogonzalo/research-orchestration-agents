"""WikiBench runner: collect suites, render reports, gate on thresholds.

Commands::

    uv run python -m knowledge_engine.scripts.wikibench.runner            # run all suites
    uv run python -m knowledge_engine.scripts.wikibench.runner --accept   # write the baseline
    uv run python -m knowledge_engine.scripts.wikibench.runner --check    # gate vs baseline

``--check`` exits non-zero on any hard-threshold violation or baseline
regression, so it can sit in ``make check``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from knowledge_engine.scripts.wikibench import thresholds
from knowledge_engine.scripts.wikibench.schemas import BenchReport
from knowledge_engine.scripts.wikibench.suites import SUITES, run_suite

BASELINE_PATH = Path("bench/wikibench/baseline.json")
REPORT_JSON_PATH = Path("bench/wikibench/report.json")
REPORT_MD_PATH = Path("bench/wikibench/report.md")
_DEFAULT_SCALE = 10
_DEFAULT_SEED = 7


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
    scale: int = _DEFAULT_SCALE,
    seed: int = _DEFAULT_SEED,
    backend: str = "file",
    tier: str = "0",
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
        "| Suite | Metric | Value |",
        "|---|---|---|",
    ]
    for suite in report.suites:
        for metric, value in sorted(suite.metrics.items()):
            lines.append(f"| {suite.name} | {metric} | {value:.4g} |")
    return "\n".join(lines) + "\n"


def _load_baseline(path: Path) -> dict[str, float] | None:
    """Load flattened baseline metrics, or None when absent.

    Args:
        path: Baseline JSON path.

    Returns:
        dict[str, float] | None: Metrics mapping or None.
    """
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in loaded.items()}


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run suites, print, and accept or check a baseline.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 green, 1 threshold/regression failure, 2 missing baseline.
    """
    parser = argparse.ArgumentParser(description="deterministic WikiBench harness")
    parser.add_argument(
        "--suite", action="append", choices=sorted(SUITES), help="suite to run (repeatable; default all)"
    )
    parser.add_argument("--scale", type=int, default=_DEFAULT_SCALE, help="corpus size")
    parser.add_argument("--seed", type=int, default=_DEFAULT_SEED, help="corpus seed")
    parser.add_argument("--backend", default="file", help="backend id")
    parser.add_argument("--tier", default="0", help="execution tier")
    parser.add_argument("--workspace", default=None, help="scratch directory (default: a temp dir)")
    parser.add_argument("--accept", action="store_true", help="write the baseline (review like code)")
    parser.add_argument("--check", action="store_true", help="gate vs the baseline")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--report", action="store_true", help="write report.json and report.md under bench/wikibench")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="wikibench-"))
    names = args.suite or sorted(SUITES)
    report = asyncio.run(
        collect(names, workspace=workspace, scale=args.scale, seed=args.seed, backend=args.backend, tier=args.tier)
    )

    for suite in report.suites:
        status = "PASS" if suite.passed else "FAIL"
        print(f"{status} {suite.name}: " + ", ".join(f"{k}={v:.4g}" for k, v in sorted(suite.metrics.items())))

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))

    if args.report:
        REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON_PATH.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        REPORT_MD_PATH.write_text(render_markdown(report), encoding="utf-8")
        print(f"report written: {REPORT_JSON_PATH} , {REPORT_MD_PATH}")

    if args.accept:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(report.metrics(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE_PATH} (commit it with this change)")
        return 0

    if args.check:
        baseline = _load_baseline(BASELINE_PATH)
        if baseline is None:
            print(f"no baseline at {BASELINE_PATH}; run --accept first")
            return 2
        failures = evaluate(report, baseline)
        for failure in failures:
            print(f"FAIL {failure}")
        print("wikibench green" if not failures else "wikibench regressed")
        return 1 if failures else 0

    return 0 if all(suite.passed for suite in report.suites) else 1


if __name__ == "__main__":
    raise SystemExit(main())

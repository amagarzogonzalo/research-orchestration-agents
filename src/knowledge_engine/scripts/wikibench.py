"""CLI entrypoint for WikiBench (presentation lives here, not in the library).

Usage::

    uv run python -m knowledge_engine.scripts.wikibench                 # all suites
    uv run python -m knowledge_engine.scripts.wikibench --use-case answer-from-wiki
    uv run python -m knowledge_engine.scripts.wikibench --accept        # write the baseline
    uv run python -m knowledge_engine.scripts.wikibench --check         # gate vs baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from knowledge_engine.wikibench import runner
from knowledge_engine.wikibench.use_cases import SUITES, USE_CASES


def _resolve_suites(args: argparse.Namespace) -> list[str]:
    """Resolve the suites to run from ``--suite``/``--use-case`` (or all).

    Args:
        args: Parsed CLI arguments.

    Returns:
        list[str]: Ordered, de-duplicated suite names.
    """
    if args.suite:
        return list(args.suite)
    if args.use_case:
        names: list[str] = []
        for use_case in args.use_case:
            for suite in USE_CASES[use_case]:
                if suite not in names:
                    names.append(suite)
        return names
    return list(SUITES)


def main(argv: list[str] | None = None) -> int:
    """Run the WikiBench CLI.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 green, 1 threshold/regression failure, 2 missing baseline.
    """
    parser = argparse.ArgumentParser(description="deterministic WikiBench harness")
    parser.add_argument("--suite", action="append", choices=sorted(SUITES), help="suite to run (repeatable)")
    parser.add_argument(
        "--use-case", action="append", choices=sorted(USE_CASES), help="run every suite of a use case (repeatable)"
    )
    parser.add_argument("--scale", type=int, default=runner.DEFAULT_SCALE, help="corpus size")
    parser.add_argument("--seed", type=int, default=runner.DEFAULT_SEED, help="corpus seed")
    parser.add_argument("--backend", default=runner.DEFAULT_BACKEND, help="backend id")
    parser.add_argument("--tier", default=runner.DEFAULT_TIER, help="execution tier")
    parser.add_argument("--workspace", default=None, help="scratch directory (default: a temp dir)")
    parser.add_argument("--accept", action="store_true", help="write the baseline (review like code)")
    parser.add_argument("--check", action="store_true", help="gate vs the baseline")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--report", action="store_true", help="write report.json and report.md under bench/wikibench")
    args = parser.parse_args(argv)

    names = _resolve_suites(args)
    workspace = Path(args.workspace) if args.workspace else Path(tempfile.mkdtemp(prefix="wikibench-"))
    report = asyncio.run(
        runner.collect(
            names, workspace=workspace, scale=args.scale, seed=args.seed, backend=args.backend, tier=args.tier
        )
    )

    for suite in report.suites:
        status = "PASS" if suite.passed else "FAIL"
        metrics = ", ".join(f"{key}={value:.4g}" for key, value in sorted(suite.metrics.items()))
        print(f"{status} {suite.use_case}/{suite.name}: {metrics}")

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))

    if args.report:
        json_path, md_path = runner.write_report(report)
        print(f"report written: {json_path} , {md_path}")

    if args.accept:
        runner.save_baseline(report)
        print(f"baseline written: {runner.BASELINE_PATH} (commit it with this change)")
        return 0

    if args.check:
        baseline = runner.load_baseline()
        if baseline is None:
            print(f"no baseline at {runner.BASELINE_PATH}; run --accept first")
            return 2
        failures = runner.evaluate(report, baseline)
        for failure in failures:
            print(f"FAIL {failure}")
        print("wikibench green" if not failures else "wikibench regressed")
        return 1 if failures else 0

    return 0 if all(suite.passed for suite in report.suites) else 1


if __name__ == "__main__":
    raise SystemExit(main())

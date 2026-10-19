#!/usr/bin/env python3
"""HTTP load probe: submit N research jobs with a bounded client concurrency.

Run it against staging or a stub, never production: once the worker executes
each job, that run spends free-tier LLM and search quota.

    uv run python bin/load_test.py --url http://127.0.0.1:8000 --token $API_TOKEN \
        --requests 20 --concurrency 4 --query "load probe: what is rag"

The endpoint is single-worker by design (Render Free), so the useful signal is
queue behavior under overload: 429s from the caps, sustained drain rate, and
end-to-end latency percentiles — not raw throughput.
"""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx


def _percentile(values: list[float], pct: float) -> float:
    """Return the nearest-rank percentile of a list.

    Args:
        values: Sample values.
        pct: Percentile in 0..100.

    Returns:
        float: Percentile value (0.0 when the sample is empty).
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[index]


async def _one(
    client: httpx.AsyncClient,
    url: str,
    token: str | None,
    query: str,
    poll_seconds: float,
    timeout_seconds: float,
) -> tuple[float, str]:
    """Submit and poll one job to a terminal state.

    Args:
        client: Shared HTTP client.
        url: Service base URL.
        token: Optional bearer token.
        query: Research query.
        poll_seconds: Delay between status polls.
        timeout_seconds: Give up after this long.

    Returns:
        tuple[float, str]: Wall-clock seconds and outcome label.
    """
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    accepted = await client.post(f"{url}/research", json={"query": query, "workflow": "qa"}, headers=headers)
    if accepted.status_code != 202:
        return time.perf_counter() - started, f"submit:{accepted.status_code}"
    task_id = accepted.json()["task_id"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        polled = await client.get(f"{url}/result/{task_id}", headers=headers)
        if polled.status_code != 200:
            return time.perf_counter() - started, f"poll:{polled.status_code}"
        status = polled.json().get("status")
        if status in ("done", "error"):
            return time.perf_counter() - started, str(status)
        await asyncio.sleep(poll_seconds)
    return time.perf_counter() - started, "timeout"


async def _run(args: argparse.Namespace) -> list[tuple[float, str]]:
    """Run the probe.

    Args:
        args: Parsed CLI arguments.

    Returns:
        list[tuple[float, str]]: One (seconds, outcome) per request.
    """
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[tuple[float, str]] = []

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def worker(index: int) -> None:
            async with semaphore:
                results.append(
                    await _one(client, args.url, args.token, f"{args.query} #{index}", args.poll, args.timeout)
                )

        await asyncio.gather(*(worker(index) for index in range(args.requests)))
    return results


def main() -> int:
    """Parse arguments, run the probe, and print a summary.

    Returns:
        int: 0 when every job finished ``done``, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description="HTTP load probe for the research service.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="service base URL")
    parser.add_argument("--token", default=None, help="bearer token (API_TOKEN)")
    parser.add_argument("--requests", type=int, default=20, help="total jobs to submit")
    parser.add_argument("--concurrency", type=int, default=4, help="maximum in-flight submissions")
    parser.add_argument("--query", default="load probe: what is retrieval augmented generation")
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between status polls")
    parser.add_argument("--timeout", type=float, default=900.0, help="per-job timeout in seconds")
    args = parser.parse_args()

    results = asyncio.run(_run(args))
    latencies = [seconds for seconds, _ in results]
    outcomes: dict[str, int] = {}
    for _, outcome in results:
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    print(f"requests={len(results)} concurrency={args.concurrency}")
    print(f"outcomes={outcomes}")
    print(
        f"latency p50={_percentile(latencies, 50):.2f}s "
        f"p95={_percentile(latencies, 95):.2f}s max={max(latencies, default=0.0):.2f}s"
    )
    return 0 if outcomes.get("done") == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

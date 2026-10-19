#!/usr/bin/env python3
"""Offline HTTP performance probe for the service (no network, no LLM keys).

Uses the real FastAPI app and worker with an inert engine double and the
in-memory job store, so it measures API/queue overhead rather than provider
latency.

    uv run python bin/api_bench.py --requests 500 --concurrency 20
    uv run python bin/api_bench.py --mode research --requests 100 --concurrency 10
"""

from __future__ import annotations

import argparse
import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx

from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import InMemoryJobStore


class BenchEngine:
    """Inert engine: instant successful runs, empty wiki."""

    async def setup(self) -> None:
        """No-op startup."""

    async def close(self) -> None:
        """No-op shutdown."""

    async def research(self, query: str, *, workflow: str = "research", **_: Any) -> Any:
        """Return an immediately completed run.

        Args:
            query: Query text.
            workflow: Workflow name.
            **_: Ignored options.

        Returns:
            Any: RunResult-like object.
        """
        _ = (query, workflow)
        state = SimpleNamespace(status=SimpleNamespace(value="done"), output_path="bench/article.md", sources=[])
        return SimpleNamespace(state=state)

    async def wiki_query(self, q: str, *, limit: int = 3) -> list[Any]:
        """Return no wiki hits.

        Args:
            q: Query.
            limit: Max hits.

        Returns:
            list[Any]: Empty list.
        """
        _ = (q, limit)
        return []

    async def wiki_page(self, slug: str) -> str | None:
        """Return no page.

        Args:
            slug: Page slug.

        Returns:
            str | None: None.
        """
        _ = slug
        return None


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile.

    Args:
        values: Samples.
        pct: Percentile 0..100.

    Returns:
        float: Percentile value (0 when empty).
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1)))]


async def _health(client: httpx.AsyncClient, sem: asyncio.Semaphore) -> tuple[float, str]:
    """One health request.

    Args:
        client: ASGI client.
        sem: Concurrency gate.

    Returns:
        tuple[float, str]: Seconds and outcome.
    """
    async with sem:
        start = time.perf_counter()
        response = await client.get("/health")
        return time.perf_counter() - start, str(response.status_code)


async def _research(client: httpx.AsyncClient, sem: asyncio.Semaphore, index: int) -> tuple[float, str]:
    """Submit and poll one research job.

    Args:
        client: ASGI client.
        sem: Concurrency gate.
        index: Request index (keeps queries distinct).

    Returns:
        tuple[float, str]: Seconds and terminal status.
    """
    async with sem:
        start = time.perf_counter()
        accepted = await client.post("/research", json={"query": f"bench query {index}", "workflow": "qa"})
        if accepted.status_code != 202:
            return time.perf_counter() - start, f"submit:{accepted.status_code}"
        task_id = accepted.json()["task_id"]
        while True:
            job = await client.get(f"/result/{task_id}")
            status = job.json().get("status")
            if status in {"done", "error"}:
                return time.perf_counter() - start, str(status)
            if time.perf_counter() - start > 30:
                return time.perf_counter() - start, "timeout"
            await asyncio.sleep(0.005)


async def _run(args: argparse.Namespace) -> list[tuple[float, str]]:
    """Run the probe.

    Args:
        args: Parsed CLI arguments.

    Returns:
        list[tuple[float, str]]: One (seconds, outcome) per request.
    """
    app = create_app(
        engine=BenchEngine(),
        jobs=InMemoryJobStore(),
        allow_anonymous=True,
        max_concurrent_jobs=args.workers,
        max_queued_jobs=0,
        max_active_jobs_per_caller=0,
        job_poll_seconds=0.005,
    )
    sem = asyncio.Semaphore(args.concurrency)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
            if args.mode == "health":
                return await asyncio.gather(*(_health(client, sem) for _ in range(args.requests)))
            return await asyncio.gather(*(_research(client, sem, i) for i in range(args.requests)))


def main() -> int:
    """Parse arguments, run, and print a summary.

    Returns:
        int: 0 when every request succeeded.
    """
    parser = argparse.ArgumentParser(description="Offline service performance probe.")
    parser.add_argument("--mode", choices=("health", "research"), default="health")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4, help="worker concurrency for research mode")
    args = parser.parse_args()

    started = time.perf_counter()
    results = asyncio.run(_run(args))
    elapsed = time.perf_counter() - started
    latencies = [seconds for seconds, _ in results]
    outcomes: dict[str, int] = {}
    for _, outcome in results:
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    print(f"mode={args.mode} requests={len(results)} concurrency={args.concurrency} workers={args.workers}")
    print(f"outcomes={outcomes} wall={elapsed:.2f}s throughput={len(results) / elapsed:.1f} req/s")
    print(
        f"latency p50={_percentile(latencies, 50) * 1000:.2f}ms "
        f"p95={_percentile(latencies, 95) * 1000:.2f}ms "
        f"p99={_percentile(latencies, 99) * 1000:.2f}ms "
        f"max={max(latencies, default=0.0) * 1000:.2f}ms"
    )
    ok = outcomes.get("200", 0) if args.mode == "health" else outcomes.get("done", 0)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

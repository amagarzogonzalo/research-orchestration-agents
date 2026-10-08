#!/usr/bin/env python3
"""Cloud health probe (issue #20): discovery + Supabase Postgres + Storage smoke.

Verifies each deployment leg in seconds::

    uv run python bin/cloud_health_check.py          # all legs
    uv run python bin/cloud_health_check.py --quick  # discovery only

Exit 0 when every leg passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

if os.name == "nt":
    # psycopg async on Windows needs the selector loop; Linux prod unaffected.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _pass(leg: str, detail: str) -> str:
    """Format a passing leg line.

    Args:
        leg: Leg name.
        detail: Detail text.

    Returns:
        str: Marked line.
    """
    return f"PASS {leg}: {detail}"


def _fail(leg: str, detail: str) -> str:
    """Format a failing leg line.

    Args:
        leg: Leg name.
        detail: Failure detail.

    Returns:
        str: Marked line.
    """
    return f"FAIL {leg}: {detail}"


async def _discovery_leg() -> str:
    """Attempt a cheap free-LLM health check.

    Returns:
        str: Result line.
    """
    from knowledge_engine.engine import ResearchEngine

    engine = ResearchEngine(store_root="research_store/health")
    try:
        model = await engine.discover()
        return _pass("discovery", f"working model: {model}") if model else _fail("discovery", "no free model")
    except Exception as exc:  # noqa: BLE001 - probe never crashes the runner
        return _fail("discovery", f"{type(exc).__name__}: {str(exc)[:150]}")


async def _postgres_leg() -> str:
    """Open the Postgres saver + store and confirm tables exist.

    Returns:
        str: Result line.
    """
    from knowledge_engine.orchestration.checkpoint import build_checkpointer, setup_postgres
    from knowledge_engine.orchestration.store_factory import build_store

    saver_cm = build_checkpointer("postgres")
    store_cm: Any = build_store("postgres")
    async with saver_cm as saver:
        async with store_cm as store:
            await setup_postgres(saver, store)
            return _pass("postgres", "pooler reachable; checkpoints/store tables ready")


async def _storage_leg() -> str:
    """Ensure the Storage buckets are present/writable.

    Returns:
        str: Result line.
    """
    from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore
    from knowledge_engine.store.supabase_body_storage import SupabaseBodyStorage

    artifacts = SupabaseArtifactStore.from_env()
    artifacts._ensure_buckets()
    SupabaseBodyStorage.from_env()._ensure_bucket()
    return _pass("storage", "buckets articles/sources/bodies present")


async def _all(quick: bool) -> list[str]:
    """Run every non-skipped leg.

    Args:
        quick: Discovery only when True.

    Returns:
        list[str]: Result lines.
    """
    out = [await _discovery_leg()]
    if not quick:
        for leg, coro in (("postgres", _postgres_leg()), ("storage", _storage_leg())):
            try:
                out.append(await coro)
            except Exception as exc:  # noqa: BLE001 - probe surfaces the failing leg
                out.append(_fail(leg, f"{type(exc).__name__}: {str(exc)[:150]}"))
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entry: verify each cloud leg and summarize.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 green, 1 any leg down.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="discovery only")
    args = parser.parse_args(argv)
    from knowledge_engine.config import load_dotenv

    if not load_dotenv():
        print(_fail("env", ".env file not found"))
        return 1
    lines = asyncio.run(_all(args.quick))
    for line in lines:
        print(line)
    return 0 if all(line.startswith("PASS") for line in lines) else 1


if __name__ == "__main__":
    raise SystemExit(main())

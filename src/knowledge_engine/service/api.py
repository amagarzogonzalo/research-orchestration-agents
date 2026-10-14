"""Uvicorn entrypoint: builds the service app from the environment (Render).

Target: ``uvicorn knowledge_engine.service.api:app``. Nothing connects at import
(building the engine just resolves kinds; ``setup()`` runs in the app lifespan).
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from knowledge_engine.config import load_dotenv
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import PostgresJobStore
from knowledge_engine.service.schemas import ReadinessReport

load_dotenv()


async def _db_ok() -> bool:
    """Ping the Postgres pooler with ``select 1``.

    Returns:
        bool: True when the database is reachable.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return False
    try:
        import psycopg

        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=5) as conn:
            await (await conn.execute("select 1")).fetchone()
        return True
    except Exception:  # noqa: BLE001 - readiness probes never raise
        return False


async def _storage_ok() -> bool:
    """List Supabase Storage buckets.

    Returns:
        bool: True when Storage is reachable.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        return False
    try:
        from supabase import create_client

        create_client(url, key).storage.list_buckets()
        return True
    except Exception:  # noqa: BLE001 - readiness probes never raise
        return False


async def _readiness() -> ReadinessReport:
    """Check the database and Storage buckets (server-side dependencies only).

    Returns:
        ReadinessReport: Named checks; ``ok`` only when all pass.
    """
    checks = {"database": await _db_ok(), "storage": await _storage_ok()}
    return ReadinessReport(ok=all(checks.values()), checks=checks)


def _build_app() -> FastAPI:
    """Compose the app: env-driven engine + Postgres jobs + readiness probe.

    Returns:
        FastAPI: The service application.
    """
    engine = ResearchEngine()
    jobs = PostgresJobStore(os.environ.get("DATABASE_URL", ""))
    return create_app(
        engine=engine,
        jobs=jobs,
        api_token=os.environ.get("API_TOKEN") or None,
        readiness_probe=_readiness,
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30")),
    )


app = _build_app()

__all__ = ["app"]

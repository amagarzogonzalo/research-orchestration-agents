"""Uvicorn entrypoint: builds the service app from the environment (Render).

Target: ``uvicorn knowledge_engine.service.api:app``. Nothing connects at import
(building the engine just resolves kinds; ``setup()`` runs in the app lifespan).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI

from knowledge_engine.config import load_dotenv
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.scripts.apply_migrations import apply
from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import build_job_store
from knowledge_engine.service.rate_limit import PostgresRateLimiter, RateLimiter
from knowledge_engine.service.schemas import ReadinessReport

logger = logging.getLogger(__name__)

# psycopg's async connections require the selector loop; Windows defaults to
# Proactor, which breaks job/readiness DB calls at startup.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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


def _list_buckets(url: str, key: str) -> bool:
    """List Supabase Storage buckets (sync; run in a worker thread).

    Args:
        url: Supabase project URL.
        key: Service-role key.

    Returns:
        bool: True when Storage is reachable.
    """
    from supabase import create_client

    create_client(url, key).storage.list_buckets()
    return True


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
        return await asyncio.to_thread(_list_buckets, url, key)
    except Exception:  # noqa: BLE001 - readiness probes never raise
        return False


_PROVIDER_KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY")


def _providers_ok() -> bool:
    """Check that at least one LLM provider key is configured.

    Readiness must mean "can serve a run", not just "can reach the database": a
    service with no provider key will 500 every ``/research`` call.

    Returns:
        bool: True when at least one provider key is present.
    """
    return any(os.environ.get(name) for name in _PROVIDER_KEYS)


def _auth_ok() -> bool:
    """Check that data routes can authenticate callers.

    Returns:
        bool: True when a write token or explicit anonymous mode is configured.
    """
    return bool(os.environ.get("API_TOKEN")) or _env_flag("ALLOW_ANONYMOUS", False)


def _migrations_dir() -> Path:
    """Return the configured migrations directory.

    Returns:
        Path: Directory holding ``*.sql`` migrations.
    """
    return Path(os.environ.get("MIGRATIONS_DIR", "supabase/migrations"))


async def _migrations_ok() -> bool:
    """Check that no database migration is pending.

    Returns:
        bool: True when the database is at the latest migration (or no database
            or migrations directory is configured).
    """
    dsn = os.environ.get("DATABASE_URL", "")
    directory = _migrations_dir()
    if not dsn or not directory.is_dir():
        return True
    try:
        pending = await asyncio.to_thread(apply, dsn, directory, dry_run=True)
    except Exception:  # noqa: BLE001 - readiness reports the failure as not-ready
        logger.exception("migration readiness check failed")
        return False
    return not pending


async def _migrate() -> None:
    """Apply pending migrations before serving (idempotent, ledger-guarded).

    A failing migration propagates and aborts startup instead of serving
    against a half-migrated schema.
    """
    if not _env_flag("AUTO_MIGRATE", True):
        return
    dsn = os.environ.get("DATABASE_URL", "")
    directory = _migrations_dir()
    if not dsn or not directory.is_dir():
        logger.warning("auto-migrate skipped (DATABASE_URL or migrations dir missing)")
        return
    applied = await asyncio.to_thread(apply, dsn, directory)
    if applied:
        logger.info("applied migrations: %s", ", ".join(applied))


async def _readiness() -> ReadinessReport:
    """Check the database, Storage buckets, auth, migrations, and providers.

    Returns:
        ReadinessReport: Named checks; ``ok`` only when all pass.
    """
    checks = {
        "database": await _db_ok(),
        "storage": await _storage_ok(),
        "providers": _providers_ok(),
        "auth": _auth_ok(),
        "migrations": await _migrations_ok(),
    }
    return ReadinessReport(ok=all(checks.values()), checks=checks)


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment flag.

    Args:
        name: Environment variable name.
        default: Value when the variable is unset.

    Returns:
        bool: The parsed flag.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a logged fallback.

    Args:
        name: Environment variable name.
        default: Value when the variable is unset or invalid.

    Returns:
        int: The parsed integer.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid integer for %s=%r; using %d", name, raw, default)
        return default


def _build_app() -> FastAPI:
    """Compose the app: env-driven engine, queue, limiter, and readiness probe.

    Returns:
        FastAPI: The service application.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    engine = ResearchEngine(cleanup_after_run=_env_flag("CLEANUP_AFTER_RUN", True))
    jobs_kind = os.environ.get("JOBS_KIND", "").strip().lower() or ("postgres" if dsn else "memory")
    jobs = build_job_store(jobs_kind, dsn)
    rate_per_minute = _env_int("RATE_LIMIT_PER_MINUTE", 30)
    limiter = (
        PostgresRateLimiter(dsn, rate_per_minute) if dsn and rate_per_minute > 0 else RateLimiter(rate_per_minute)
    )
    timeout_seconds = _env_int("JOB_TIMEOUT_SECONDS", 900)
    stale_default = timeout_seconds + 60 if timeout_seconds > 0 else 960
    return create_app(
        engine=engine,
        jobs=jobs,
        api_token=os.environ.get("API_TOKEN") or None,
        read_token=os.environ.get("API_TOKEN_READONLY") or None,
        readiness_probe=_readiness,
        rate_limiter=limiter,
        job_timeout_seconds=timeout_seconds,
        job_stale_seconds=_env_int("JOB_STALE_SECONDS", stale_default),
        job_poll_seconds=float(_env_int("JOB_POLL_MS", 500)) / 1000.0,
        job_max_attempts=_env_int("JOB_MAX_ATTEMPTS", 3),
        max_concurrent_jobs=_env_int("MAX_CONCURRENT_JOBS", 1),
        max_queued_jobs=_env_int("MAX_QUEUED_JOBS", 16),
        max_active_jobs_per_caller=_env_int("MAX_ACTIVE_JOBS_PER_CALLER", 4),
        allow_anonymous=_env_flag("ALLOW_ANONYMOUS", False),
        startup_hook=_migrate if dsn else None,
        enable_docs=_env_flag("ENABLE_DOCS", False),
    )


app = _build_app()

__all__ = ["app"]

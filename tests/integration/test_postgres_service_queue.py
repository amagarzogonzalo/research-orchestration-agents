"""Real-Postgres tests for the service queue + shared limiter (skip when absent).

Runs against ``WIKI_TEST_PG_DSN`` (compose scratch DB or the CI service
container); skipped cleanly when no database is reachable, like the wiki v2
integration suite.
"""

from __future__ import annotations

import os

import pytest

from knowledge_engine.service.jobs import PostgresJobStore
from knowledge_engine.service.rate_limit import PostgresRateLimiter
from knowledge_engine.service.schemas import JobRecord, JobStatus

_PG_DSN = os.environ.get("WIKI_TEST_PG_DSN", "postgresql://wiki:wiki@127.0.0.1:54329/wiki")


def _pg_available() -> bool:
    """Probe the test DSN once.

    Returns:
        bool: True when a connection succeeds.
    """
    try:
        import psycopg

        with psycopg.connect(_PG_DSN, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001 - a probe failure means "skip"
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="WIKI_TEST_PG_DSN not reachable")


def _record(task_id: str, *, caller: str = "caller-a") -> JobRecord:
    """Build a queued job record.

    Args:
        task_id: Job id.
        caller: Caller key.

    Returns:
        JobRecord: A queued record.
    """
    return JobRecord(
        task_id=task_id,
        status=JobStatus.QUEUED,
        thread_id=task_id,
        query="q",
        workflow="qa",
        settings={"max_subqueries": 2},
        caller=caller,
    )


async def _store() -> PostgresJobStore:
    """Create a store with a clean ``service_jobs`` table.

    Returns:
        PostgresJobStore: The prepared store.
    """
    import psycopg

    store = PostgresJobStore(_PG_DSN)
    await store.ensure_schema()
    with psycopg.connect(_PG_DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE service_jobs")
    return store


async def test_claim_is_fifo_and_records_attempts() -> None:
    """Jobs are claimed oldest-first and each claim increments attempts."""
    store = await _store()
    await store.create(_record("t1"))
    await store.create(_record("t2"))
    first = await store.claim_next()
    assert first is not None
    assert first.task_id == "t1"
    assert first.status == JobStatus.RUNNING
    assert first.attempts == 1
    assert first.settings == {"max_subqueries": 2}
    second = await store.claim_next()
    assert second is not None and second.task_id == "t2"
    assert await store.claim_next() is None


async def test_requeue_returns_running_job_to_the_queue() -> None:
    """A graceful-shutdown requeue puts the job back without losing settings."""
    store = await _store()
    await store.create(_record("t1"))
    claimed = await store.claim_next()
    assert claimed is not None
    assert await store.requeue("t1") is True
    again = await store.claim_next()
    assert again is not None and again.attempts == 2


async def test_stale_running_job_is_recovered_then_failed_at_max_attempts() -> None:
    """Abandoned claims are requeued until the attempt cap, then failed."""
    import psycopg

    store = await _store()
    await store.create(_record("t1"))
    await store.claim_next()
    with psycopg.connect(_PG_DSN, autocommit=True) as conn:
        conn.execute("UPDATE service_jobs SET claimed_at = now() - interval '2 hours' WHERE task_id = 't1'")
    assert await store.recover_stale(stale_after_seconds=60, max_attempts=3) == 1
    record = await store.get("t1")
    assert record is not None and record.status == JobStatus.QUEUED

    for _ in range(2):
        await store.claim_next()
        with psycopg.connect(_PG_DSN, autocommit=True) as conn:
            conn.execute("UPDATE service_jobs SET claimed_at = now() - interval '2 hours' WHERE task_id = 't1'")
        await store.recover_stale(stale_after_seconds=60, max_attempts=3)
    record = await store.get("t1")
    assert record is not None and record.status == JobStatus.ERROR
    assert "max attempts" in (record.error or "")


async def test_counts_scope_by_caller() -> None:
    """Queue depth and per-caller active counts are queryable."""
    store = await _store()
    await store.create(_record("t1", caller="a"))
    await store.create(_record("t2", caller="a"))
    await store.create(_record("t3", caller="b"))
    assert await store.count_queued() == 3
    assert await store.count_active() == 3
    assert await store.count_active(caller="a") == 2
    await store.claim_next()
    assert await store.count_queued() == 2
    assert await store.count_active() == 3


def test_postgres_rate_limiter_shares_the_window() -> None:
    """The shared limiter allows up to the limit per key and reports retry_after."""
    import psycopg

    limiter = PostgresRateLimiter(_PG_DSN, 2)
    limiter.ensure_schema()
    with psycopg.connect(_PG_DSN, autocommit=True) as conn:
        conn.execute("TRUNCATE TABLE service_rate_limits")
    assert limiter.allow("caller-hash") is True
    assert limiter.allow("caller-hash") is True
    assert limiter.allow("caller-hash") is False
    assert limiter.retry_after("caller-hash") > 0
    assert limiter.allow("other-hash") is True

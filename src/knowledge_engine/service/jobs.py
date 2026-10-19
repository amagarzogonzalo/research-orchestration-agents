"""Durable job store for the HTTP service (in-memory + Postgres).

Render may recycle the process mid-run, so job status must outlive the worker:
the Postgres store writes a small ``service_jobs`` table and exposes an atomic
claim/requeue queue. The in-memory store backs offline tests.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime, timedelta
from typing import Any

from knowledge_engine.service.schemas import JobRecord, JobStatus

_COLUMNS = (
    "task_id",
    "status",
    "thread_id",
    "query",
    "workflow",
    "result",
    "error",
    "settings",
    "caller",
    "attempts",
    "claimed_at",
)
_COLUMNS_SQL = ", ".join(_COLUMNS)
_COLUMNS_QUALIFIED = ", ".join(f"j.{column}" for column in _COLUMNS)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS service_jobs (
        task_id    text PRIMARY KEY,
        status     text NOT NULL,
        thread_id  text NOT NULL,
        query      text NOT NULL,
        workflow   text NOT NULL,
        result     jsonb,
        error      text,
        settings   jsonb,
        caller     text,
        attempts   integer NOT NULL DEFAULT 0,
        claimed_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE service_jobs ADD COLUMN IF NOT EXISTS settings jsonb",
    "ALTER TABLE service_jobs ADD COLUMN IF NOT EXISTS caller text",
    "ALTER TABLE service_jobs ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0",
    "ALTER TABLE service_jobs ADD COLUMN IF NOT EXISTS claimed_at timestamptz",
    "CREATE INDEX IF NOT EXISTS service_jobs_queue_idx ON service_jobs (status, updated_at)",
)


def _utcnow() -> datetime:
    """Return the current UTC timestamp.

    Returns:
        datetime: Timezone-aware UTC timestamp.
    """
    return datetime.now(UTC)


def _row_to_record(row: Any) -> JobRecord:
    """Map a ``service_jobs`` row to a :class:`JobRecord`.

    Args:
        row: Positional row in ``_COLUMNS`` order.

    Returns:
        JobRecord: The validated job record.
    """
    return JobRecord(
        task_id=row[0],
        status=JobStatus(row[1]),
        thread_id=row[2],
        query=row[3],
        workflow=row[4],
        result=row[5],
        error=row[6],
        settings=row[7],
        caller=row[8],
        attempts=int(row[9] or 0),
        claimed_at=row[10],
    )


class JobStore(abc.ABC):
    """Persistence and queue contract for research jobs."""

    @abc.abstractmethod
    async def create(self, record: JobRecord) -> None:
        """Persist a newly accepted job.

        Args:
            record: The initial job record (``queued``).
        """
        ...

    @abc.abstractmethod
    async def get(self, task_id: str) -> JobRecord | None:
        """Return a job by id, or None when absent.

        Args:
            task_id: Server-generated job id.

        Returns:
            JobRecord | None: The job or None.
        """
        ...

    @abc.abstractmethod
    async def update(
        self,
        task_id: str,
        *,
        status: JobStatus | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Merge new fields into an existing job.

        Args:
            task_id: Job id.
            status: New status, when changing.
            result: New result summary, when completing.
            error: New error detail, when failing.
        """
        ...

    @abc.abstractmethod
    async def claim_next(self) -> JobRecord | None:
        """Atomically claim the oldest queued job.

        Returns:
            JobRecord | None: The claimed job, or None when the queue is empty.
        """
        ...

    @abc.abstractmethod
    async def requeue(self, task_id: str) -> bool:
        """Return a running job to the queue (graceful shutdown).

        Args:
            task_id: Job id.

        Returns:
            bool: True when a running job was requeued.
        """
        ...

    @abc.abstractmethod
    async def recover_stale(self, *, stale_after_seconds: float, max_attempts: int) -> int:
        """Requeue running jobs whose worker disappeared.

        Args:
            stale_after_seconds: Age after which a running claim is stale.
            max_attempts: Attempts after which the job is failed instead.

        Returns:
            int: Number of jobs recovered or failed.
        """
        ...

    @abc.abstractmethod
    async def count_active(self, *, caller: str | None = None) -> int:
        """Count queued and running jobs.

        Args:
            caller: Optional caller key to scope the count.

        Returns:
            int: Number of active jobs.
        """
        ...

    @abc.abstractmethod
    async def count_queued(self) -> int:
        """Count jobs waiting to be claimed.

        Returns:
            int: Number of queued jobs.
        """
        ...


class InMemoryJobStore(JobStore):
    """Process-local job store (tests and single-process fallback)."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    async def create(self, record: JobRecord) -> None:
        """Store the job.

        Args:
            record: Initial job record.
        """
        self._jobs[record.task_id] = record

    async def get(self, task_id: str) -> JobRecord | None:
        """Return the job, or None.

        Args:
            task_id: Job id.

        Returns:
            JobRecord | None: The job or None.
        """
        return self._jobs.get(task_id)

    async def update(
        self,
        task_id: str,
        *,
        status: JobStatus | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Merge fields into the stored job.

        Args:
            task_id: Job id.
            status: New status, when changing.
            result: New result summary, when completing.
            error: New error detail, when failing.
        """
        record = self._jobs.get(task_id)
        if record is None:
            return
        update: dict[str, Any] = {}
        if status is not None:
            update["status"] = status
        if result is not None:
            update["result"] = result
        if error is not None:
            update["error"] = error
        self._jobs[task_id] = record.model_copy(update=update)

    async def claim_next(self) -> JobRecord | None:
        """Claim the oldest queued job.

        Returns:
            JobRecord | None: The claimed job, or None when the queue is empty.
        """
        for task_id, record in self._jobs.items():
            if record.status == JobStatus.QUEUED:
                claimed = record.model_copy(
                    update={
                        "status": JobStatus.RUNNING,
                        "claimed_at": _utcnow(),
                        "attempts": record.attempts + 1,
                    }
                )
                self._jobs[task_id] = claimed
                return claimed
        return None

    async def requeue(self, task_id: str) -> bool:
        """Return a running job to the queue.

        Args:
            task_id: Job id.

        Returns:
            bool: True when a running job was requeued.
        """
        record = self._jobs.get(task_id)
        if record is None or record.status != JobStatus.RUNNING:
            return False
        self._jobs[task_id] = record.model_copy(update={"status": JobStatus.QUEUED, "claimed_at": None})
        return True

    async def recover_stale(self, *, stale_after_seconds: float, max_attempts: int) -> int:
        """Requeue running jobs whose worker disappeared.

        Args:
            stale_after_seconds: Age after which a running claim is stale.
            max_attempts: Attempts after which the job is failed instead.

        Returns:
            int: Number of jobs recovered or failed.
        """
        cutoff = _utcnow() - timedelta(seconds=stale_after_seconds)
        recovered = 0
        for task_id, record in list(self._jobs.items()):
            if record.status != JobStatus.RUNNING:
                continue
            if record.claimed_at is not None and record.claimed_at >= cutoff:
                continue
            if record.attempts >= max_attempts:
                self._jobs[task_id] = record.model_copy(
                    update={
                        "status": JobStatus.ERROR,
                        "claimed_at": None,
                        "error": f"recovered after {record.attempts} attempts",
                    }
                )
            else:
                self._jobs[task_id] = record.model_copy(update={"status": JobStatus.QUEUED, "claimed_at": None})
            recovered += 1
        return recovered

    async def count_active(self, *, caller: str | None = None) -> int:
        """Count queued and running jobs.

        Args:
            caller: Optional caller key to scope the count.

        Returns:
            int: Number of active jobs.
        """
        return sum(
            1
            for record in self._jobs.values()
            if record.status in (JobStatus.QUEUED, JobStatus.RUNNING) and (caller is None or record.caller == caller)
        )

    async def count_queued(self) -> int:
        """Count jobs waiting to be claimed.

        Returns:
            int: Number of queued jobs.
        """
        return sum(1 for record in self._jobs.values() if record.status == JobStatus.QUEUED)


class PostgresJobStore(JobStore):
    """Supabase Postgres-backed job store (durable across restarts).

    Args:
        conn_string: Session-pooler DSN (``DATABASE_URL``).
    """

    def __init__(self, conn_string: str) -> None:
        self._conn_string = conn_string

    async def ensure_schema(self) -> None:
        """Create or upgrade the ``service_jobs`` table (idempotent)."""
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            for statement in _SCHEMA_STATEMENTS:
                await conn.execute(statement)
            await conn.commit()

    async def create(self, record: JobRecord) -> None:
        """Insert a new job row.

        Args:
            record: Initial job record.
        """
        import psycopg
        from psycopg.types.json import Jsonb

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            await conn.execute(
                "INSERT INTO service_jobs (task_id, status, thread_id, query, workflow, result, error,"
                " settings, caller) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record.task_id,
                    record.status.value,
                    record.thread_id,
                    record.query,
                    record.workflow,
                    Jsonb(record.result) if record.result is not None else None,
                    record.error,
                    Jsonb(record.settings) if record.settings is not None else None,
                    record.caller,
                ),
            )
            await conn.commit()

    async def get(self, task_id: str) -> JobRecord | None:
        """Read a job row.

        Args:
            task_id: Job id.

        Returns:
            JobRecord | None: The job or None.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            cursor = await conn.execute(f"SELECT {_COLUMNS_SQL} FROM service_jobs WHERE task_id = %s", (task_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    async def update(
        self,
        task_id: str,
        *,
        status: JobStatus | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Merge fields into a job row.

        Args:
            task_id: Job id.
            status: New status, when changing.
            result: New result summary, when completing.
            error: New error detail, when failing.
        """
        import psycopg
        from psycopg.types.json import Jsonb

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            await conn.execute(
                "UPDATE service_jobs SET"
                " status = COALESCE(%s, status),"
                " result = COALESCE(%s, result),"
                " error = COALESCE(%s, error),"
                " updated_at = now()"
                " WHERE task_id = %s",
                (
                    status.value if status is not None else None,
                    Jsonb(result) if result is not None else None,
                    error,
                    task_id,
                ),
            )
            await conn.commit()

    async def claim_next(self) -> JobRecord | None:
        """Atomically claim the oldest queued job.

        Returns:
            JobRecord | None: The claimed job, or None when the queue is empty.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            cursor = await conn.execute(
                "WITH next AS ("
                "  SELECT task_id FROM service_jobs"
                "  WHERE status = 'queued'"
                "  ORDER BY updated_at, task_id"
                "  FOR UPDATE SKIP LOCKED"
                "  LIMIT 1"
                ")"
                " UPDATE service_jobs AS j"
                " SET status = 'running', claimed_at = now(), attempts = j.attempts + 1, updated_at = now()"
                " FROM next"
                " WHERE j.task_id = next.task_id"
                f" RETURNING {_COLUMNS_QUALIFIED}"
            )
            row = await cursor.fetchone()
            await conn.commit()
        if row is None:
            return None
        return _row_to_record(row)

    async def requeue(self, task_id: str) -> bool:
        """Return a running job to the queue.

        Args:
            task_id: Job id.

        Returns:
            bool: True when a running job was requeued.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            cursor = await conn.execute(
                "UPDATE service_jobs SET status = 'queued', claimed_at = NULL, updated_at = now()"
                " WHERE task_id = %s AND status = 'running'",
                (task_id,),
            )
            requeued = cursor.rowcount > 0
            await conn.commit()
        return requeued

    async def recover_stale(self, *, stale_after_seconds: float, max_attempts: int) -> int:
        """Requeue running jobs whose worker disappeared.

        Args:
            stale_after_seconds: Age after which a running claim is stale.
            max_attempts: Attempts after which the job is failed instead.

        Returns:
            int: Number of jobs recovered or failed.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            cursor = await conn.execute(
                "UPDATE service_jobs SET"
                " status = CASE WHEN attempts >= %s THEN 'error' ELSE 'queued' END,"
                " error = CASE WHEN attempts >= %s THEN 'recovered after max attempts' ELSE error END,"
                " claimed_at = NULL,"
                " updated_at = now()"
                " WHERE status = 'running'"
                " AND (claimed_at IS NULL OR claimed_at < now() - (%s * interval '1 second'))",
                (max_attempts, max_attempts, stale_after_seconds),
            )
            recovered = int(cursor.rowcount)
            await conn.commit()
        return recovered

    async def count_active(self, *, caller: str | None = None) -> int:
        """Count queued and running jobs.

        Args:
            caller: Optional caller key to scope the count.

        Returns:
            int: Number of active jobs.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            if caller is None:
                cursor = await conn.execute("SELECT count(*) FROM service_jobs WHERE status IN ('queued', 'running')")
            else:
                cursor = await conn.execute(
                    "SELECT count(*) FROM service_jobs WHERE status IN ('queued', 'running') AND caller = %s",
                    (caller,),
                )
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def count_queued(self) -> int:
        """Count jobs waiting to be claimed.

        Returns:
            int: Number of queued jobs.
        """
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            cursor = await conn.execute("SELECT count(*) FROM service_jobs WHERE status = 'queued'")
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0


def build_job_store(kind: str, conn_string: str) -> JobStore:
    """Build the job store for an explicit kind.

    Args:
        kind: ``memory`` or ``postgres``.
        conn_string: Postgres DSN (required for ``postgres``).

    Returns:
        JobStore: The configured store.

    Raises:
        ValueError: For an unknown kind or a missing Postgres DSN.
    """
    normalized = kind.strip().lower()
    if normalized == "memory":
        return InMemoryJobStore()
    if normalized == "postgres":
        if not conn_string:
            raise ValueError("JOBS_KIND=postgres requires DATABASE_URL")
        return PostgresJobStore(conn_string)
    raise ValueError(f"unknown JOBS_KIND: {kind!r} (use 'memory' or 'postgres')")


__all__ = ["InMemoryJobStore", "JobStore", "PostgresJobStore", "build_job_store"]

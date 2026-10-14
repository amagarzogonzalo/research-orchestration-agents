"""Durable job store for the HTTP service (in-memory + Postgres).

Render may recycle the process mid-run, so job status must outlive the worker:
the Postgres store writes a small ``service_jobs`` table. The in-memory store
backs offline tests.
"""

from __future__ import annotations

import abc
from typing import Any

from knowledge_engine.service.schemas import JobRecord, JobStatus

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS service_jobs (
    task_id    text PRIMARY KEY,
    status     text NOT NULL,
    thread_id  text NOT NULL,
    query      text NOT NULL,
    workflow   text NOT NULL,
    result     jsonb,
    error      text,
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""


class JobStore(abc.ABC):
    """Persistence contract for research jobs."""

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


class PostgresJobStore(JobStore):
    """Supabase Postgres-backed job store (durable across restarts).

    Args:
        conn_string: Session-pooler DSN (``DATABASE_URL``).
    """

    def __init__(self, conn_string: str) -> None:
        self._conn_string = conn_string

    async def ensure_schema(self) -> None:
        """Create the ``service_jobs`` table when absent (idempotent)."""
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            await conn.execute(_TABLE_DDL)
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
                "INSERT INTO service_jobs (task_id, status, thread_id, query, workflow, result, error)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    record.task_id,
                    record.status.value,
                    record.thread_id,
                    record.query,
                    record.workflow,
                    Jsonb(record.result),
                    record.error,
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
            row = await (
                await conn.execute(
                    "SELECT task_id, status, thread_id, query, workflow, result, error"
                    " FROM service_jobs WHERE task_id = %s",
                    (task_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return JobRecord(
            task_id=row[0],
            status=JobStatus(row[1]),
            thread_id=row[2],
            query=row[3],
            workflow=row[4],
            result=row[5],
            error=row[6],
        )

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


__all__ = ["InMemoryJobStore", "JobStore", "PostgresJobStore"]

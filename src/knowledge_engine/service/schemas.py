"""HTTP service contracts: research requests, durable jobs, readiness."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    """Lifecycle of a submitted research job.

    Attributes:
        QUEUED: Accepted, not started.
        RUNNING: The workflow is executing.
        DONE: Completed; ``result`` holds a summary.
        ERROR: Failed; ``error`` holds the reason.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class ResearchRequest(BaseModel):
    """Body of ``POST /research``.

    Attributes:
        model_config: Forbid extra keys.
        query: Research question.
        workflow: One of ``research``/``qa``/``summary``/``deep``.
        thread_id: Optional stable thread id (derived when omitted).
        settings: Optional per-run overrides (``RunSettings`` fields).
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    workflow: str = "research"
    thread_id: str | None = None
    settings: dict[str, Any] | None = None


class JobRecord(BaseModel):
    """Durable job state read by ``GET /result/{task_id}``.

    Attributes:
        model_config: Forbid extra keys.
        task_id: Server-generated id.
        status: Job lifecycle status.
        thread_id: LangGraph thread the job runs on.
        query: Original query.
        workflow: Workflow name.
        result: Completed-run summary (status/output_path/sources).
        error: Failure detail when ``status`` is ``error``.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: JobStatus
    thread_id: str
    query: str
    workflow: str
    result: dict[str, Any] | None = None
    error: str | None = None


class ResearchAccepted(BaseModel):
    """202 response to ``POST /research``.

    Attributes:
        model_config: Forbid extra keys.
        task_id: Id to poll via ``GET /result/{task_id}``.
        status: Initial job status (``queued``).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: JobStatus


class ReadinessReport(BaseModel):
    """Result of ``GET /readiness``.

    Attributes:
        model_config: Forbid extra keys.
        ok: True only when every check passed.
        checks: Named boolean checks (database, storage, providers).
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    checks: dict[str, bool] = Field(default_factory=dict)


class WikiIngestRequest(BaseModel):
    """Body of ``POST /wiki/ingest``.

    Attributes:
        model_config: Forbid extra keys.
        thread_id: Run thread identifier (provenance).
        query: Original research query.
        article_path: Path of the rendered ``article.md``.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    query: str
    article_path: str


__all__ = [
    "JobRecord",
    "JobStatus",
    "ReadinessReport",
    "ResearchAccepted",
    "ResearchRequest",
    "WikiIngestRequest",
]

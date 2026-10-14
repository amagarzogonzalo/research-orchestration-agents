"""FastAPI application factory for the research + wiki HTTP service (issue #30).

Framework-free core: this module takes an engine + job store and returns an app.
The uvicorn entrypoint (:mod:`knowledge_engine.service.api`) wires the real ones.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request

from knowledge_engine.service.jobs import JobStore
from knowledge_engine.service.rate_limit import RateLimiter
from knowledge_engine.service.schemas import (
    JobRecord,
    JobStatus,
    ReadinessReport,
    ResearchAccepted,
    ResearchRequest,
    WikiIngestRequest,
)

logger = logging.getLogger(__name__)


def _settings(raw: dict[str, Any] | None) -> Any:
    """Build ``RunSettings`` from a raw mapping (None stays None).

    Args:
        raw: Settings mapping or None.

    Returns:
        Any: A ``RunSettings`` instance or None.
    """
    if raw is None:
        return None
    from knowledge_engine.schemas.settings import RunSettings

    return RunSettings(**raw)


async def _run_job(engine: Any, jobs: JobStore, record: JobRecord, settings_raw: dict[str, Any] | None) -> None:
    """Execute one research job and persist its terminal status.

    Args:
        engine: The research engine facade.
        jobs: Job store to update.
        record: The job to run.
        settings_raw: Raw per-run settings mapping, or None.
    """
    await jobs.update(record.task_id, status=JobStatus.RUNNING)
    try:
        result = await engine.research(
            record.query, workflow=record.workflow, thread_id=record.thread_id, settings=_settings(settings_raw)
        )
        summary = {
            "status": result.state.status.value,
            "output_path": result.state.output_path,
            "sources": len(result.state.sources),
        }
        await jobs.update(record.task_id, status=JobStatus.DONE, result=summary)
    except Exception as exc:  # noqa: BLE001 - surface the failure to the poll endpoint
        logger.exception("research job %s failed", record.task_id)
        await jobs.update(record.task_id, status=JobStatus.ERROR, error=f"{type(exc).__name__}: {exc}")


def create_app(
    *,
    engine: Any,
    jobs: JobStore,
    api_token: str | None = None,
    readiness_probe: Callable[[], Awaitable[ReadinessReport]] | None = None,
    rate_limit_per_minute: int = 0,
) -> FastAPI:
    """Build the FastAPI app around an engine + job store.

    Args:
        engine: Research engine facade (``ResearchEngine`` or a test double).
        jobs: Durable job store.
        api_token: When set, mutating routes require ``Authorization: Bearer``.
        readiness_probe: Optional readiness check; defaults to always-ok.
        rate_limit_per_minute: Per-key sliding-window limit (<= 0 disables).

    Returns:
        FastAPI: The configured application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Enter engine resources and ensure the job schema.

        Args:
            app: The application.

        Yields:
            None: Control while the app is serving.
        """
        setup = getattr(engine, "setup", None)
        if setup is not None:
            await setup()
        ensure = getattr(jobs, "ensure_schema", None)
        if ensure is not None:
            await ensure()
        yield
        close = getattr(engine, "close", None)
        if close is not None:
            await close()

    app = FastAPI(title="Knowledge Engine", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.jobs = jobs
    limiter = RateLimiter(rate_limit_per_minute)

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        """Reject mutating calls without the bearer token (when configured).

        Args:
            authorization: The ``Authorization`` header value.

        Raises:
            HTTPException: 401 when the token is missing or wrong.
        """
        if api_token is None:
            return
        if authorization != f"Bearer {api_token}":
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    async def throttle(request: Request) -> None:
        """Reject requests over the per-key sliding-window rate limit.

        Args:
            request: The incoming request (keyed on the bearer token or host).

        Raises:
            HTTPException: 429 when the caller exceeds the limit.
        """
        if not limiter.enabled:
            return
        key = request.headers.get("authorization") or (request.client.host if request.client else "unknown")
        if not limiter.allow(key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    async def probe() -> ReadinessReport:
        """Return the readiness report (injected probe or always-ok).

        Returns:
            ReadinessReport: The readiness result.
        """
        if readiness_probe is not None:
            return await readiness_probe()
        return ReadinessReport(ok=True, checks={})

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: the process is up.

        Returns:
            dict[str, str]: Static ok payload.
        """
        return {"status": "ok", "engine": "ready"}

    @app.get("/readiness", response_model=ReadinessReport)
    async def readiness() -> ReadinessReport:
        """Readiness: Supabase buckets + DB reachable.

        Returns:
            ReadinessReport: The readiness result.
        """
        return await probe()

    @app.post(
        "/research",
        status_code=202,
        response_model=ResearchAccepted,
        dependencies=[Depends(require_token), Depends(throttle)],
    )
    async def research(req: ResearchRequest, background: BackgroundTasks) -> ResearchAccepted:
        """Accept a research run and return a job id (202).

        Args:
            req: The research request.
            background: FastAPI background task queue.

        Returns:
            ResearchAccepted: The accepted job id + status.
        """
        task_id = uuid.uuid4().hex
        record = JobRecord(
            task_id=task_id,
            status=JobStatus.QUEUED,
            thread_id=req.thread_id or task_id,
            query=req.query,
            workflow=req.workflow,
        )
        await jobs.create(record)
        background.add_task(_run_job, engine, jobs, record, req.settings)
        return ResearchAccepted(task_id=task_id, status=record.status)

    @app.get("/result/{task_id}", response_model=JobRecord)
    async def result(task_id: str) -> JobRecord:
        """Poll a job's durable status.

        Args:
            task_id: Job id.

        Returns:
            JobRecord: The current job state.

        Raises:
            HTTPException: 404 when the id is unknown.
        """
        record = await jobs.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown task id")
        return record

    @app.get("/wiki/query", dependencies=[Depends(require_token), Depends(throttle)])
    async def wiki_query(q: str = Query(...), limit: int = 3) -> dict[str, Any]:
        """Answer from the compiled wiki with no live call (token-gated).

        Args:
            q: Query text.
            limit: Maximum hits.

        Returns:
            dict[str, Any]: ``{"hits": [...]}``.
        """
        hits = await engine.wiki_query(q, limit=limit)
        return {"hits": [hit.model_dump() for hit in hits]}

    @app.post("/wiki/ingest", dependencies=[Depends(require_token), Depends(throttle)])
    async def wiki_ingest(req: WikiIngestRequest) -> dict[str, Any]:
        """Re-ingest a raw run into the wiki.

        Args:
            req: The ingest request.

        Returns:
            dict[str, Any]: The ingest result.
        """
        ingested = await engine.wiki_ingest(req.thread_id, req.query, req.article_path)
        return ingested.model_dump()

    @app.post("/wiki/lint", dependencies=[Depends(require_token), Depends(throttle)])
    async def wiki_lint() -> dict[str, Any]:
        """Run the deterministic wiki lint.

        Returns:
            dict[str, Any]: The lint report.
        """
        report = await engine.wiki_lint()
        return report.model_dump()

    return app


__all__ = ["create_app"]

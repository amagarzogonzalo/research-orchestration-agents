"""FastAPI application factory for the research + wiki HTTP service (issue #30).

Framework-free core: this module takes an engine + job store and returns an app.
The uvicorn entrypoint (:mod:`knowledge_engine.service.api`) wires the real ones.

Production behavior baked in here:

* auth fails closed when no token is configured (``allow_anonymous`` is for
  local development only);
* jobs are persisted first and executed by an in-process worker that claims
  atomically from the store, so a restart resumes queued jobs instead of
  leaving them stuck;
* queue depth and per-caller active jobs are capped, and the rate limiter can
  be swapped for the shared Postgres implementation;
* bearer tokens are hashed before they are used as limiter/quota keys.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import math
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from knowledge_engine.core.exceptions import WikiArticleMissing
from knowledge_engine.schemas.ids import validate_wiki_slug
from knowledge_engine.schemas.settings import RunSettings
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

_RECOVERY_INTERVAL_SECONDS = 60.0
_POLL_FLOOR_SECONDS = 0.05


def _digest(value: str) -> str:
    """Hash a bearer token or host before using it as a limiter/quota key.

    Args:
        value: Raw key material.

    Returns:
        str: Truncated SHA-256 digest.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _caller_id(request: Request) -> str:
    """Derive a stable caller key without persisting the raw token.

    Args:
        request: Incoming request.

    Returns:
        str: Hashed authorization header, or hashed client host.
    """
    authorization = request.headers.get("authorization")
    if authorization:
        return _digest(authorization)
    host = request.client.host if request.client else "unknown"
    return _digest(f"ip:{host}")


def _artifact_store(engine: Any) -> Any | None:
    """Return the engine's artifact store when one is configured.

    Args:
        engine: The research engine facade (or a test double).

    Returns:
        Any | None: An ``ArtifactStorePort``-like object, or None.
    """
    store = getattr(engine, "store", None)
    return getattr(store, "artifacts", None)


async def _safe_update(jobs: JobStore, task_id: str, **fields: Any) -> None:
    """Update a job, logging instead of crashing the worker on a store outage.

    Args:
        jobs: Job store.
        task_id: Job id.
        **fields: Fields forwarded to :meth:`JobStore.update`.
    """
    try:
        await jobs.update(task_id, **fields)
    except Exception:  # noqa: BLE001 - a store outage must not kill the worker
        logger.exception("failed to update job %s", task_id)


async def _execute_job(
    engine: Any,
    jobs: JobStore,
    record: JobRecord,
    timeout_seconds: float = 0,
) -> None:
    """Execute one claimed job and persist its terminal status.

    Args:
        engine: The research engine facade.
        jobs: Job store to update.
        record: The claimed job (already ``running``).
        timeout_seconds: Abort the run after this many seconds (<= 0 disables).

    Raises:
        asyncio.CancelledError: On worker shutdown; the job is requeued first.
    """
    try:
        settings = RunSettings.model_validate(record.settings) if record.settings else None
        coro = engine.research(record.query, workflow=record.workflow, thread_id=record.thread_id, settings=settings)
        result = await (asyncio.wait_for(coro, timeout=timeout_seconds) if timeout_seconds > 0 else coro)
        summary = {
            "status": result.state.status.value,
            "output_path": result.state.output_path,
            "sources": len(result.state.sources),
        }
        await _safe_update(jobs, record.task_id, status=JobStatus.DONE, result=summary)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await jobs.requeue(record.task_id)
        raise
    except TimeoutError:
        logger.warning("research job %s timed out after %ss", record.task_id, timeout_seconds)
        await _safe_update(jobs, record.task_id, status=JobStatus.ERROR, error=f"timed out after {timeout_seconds}s")
    except Exception as exc:  # noqa: BLE001 - surface the failure to the poll endpoint
        logger.exception("research job %s failed", record.task_id)
        await _safe_update(jobs, record.task_id, status=JobStatus.ERROR, error=f"{type(exc).__name__}: {exc}")


async def _worker_loop(
    engine: Any,
    jobs: JobStore,
    *,
    max_concurrent: int,
    timeout_seconds: float,
    poll_seconds: float,
    stale_seconds: float,
    max_attempts: int,
    stop: asyncio.Event,
) -> None:
    """Claim and execute jobs until shutdown, recovering abandoned ones.

    Args:
        engine: The research engine facade.
        jobs: Durable job store.
        max_concurrent: Maximum jobs executed at once.
        timeout_seconds: Per-job timeout (<= 0 disables).
        poll_seconds: Idle poll interval.
        stale_seconds: Age after which a running claim is considered abandoned.
        max_attempts: Attempts after which a recovered job is failed.
        stop: Event set by the lifespan on shutdown.
    """
    running: set[asyncio.Task[None]] = set()
    loop = asyncio.get_running_loop()
    last_recovery = loop.time()
    try:
        while not stop.is_set():
            now = loop.time()
            if now - last_recovery >= _RECOVERY_INTERVAL_SECONDS:
                try:
                    recovered = await jobs.recover_stale(
                        stale_after_seconds=stale_seconds,
                        max_attempts=max_attempts,
                    )
                    if recovered:
                        logger.warning("recovered %d stale job(s)", recovered)
                except Exception:  # noqa: BLE001 - recovery retries next interval
                    logger.exception("stale job recovery failed")
                last_recovery = now
            while len(running) < max_concurrent and not stop.is_set():
                try:
                    record = await jobs.claim_next()
                except Exception:  # noqa: BLE001 - claim retries next poll
                    logger.exception("job claim failed")
                    break
                if record is None:
                    break
                task = asyncio.create_task(
                    _execute_job(engine, jobs, record, timeout_seconds),
                    name=f"job-{record.task_id}",
                )
                running.add(task)
                task.add_done_callback(running.discard)
            await asyncio.sleep(poll_seconds)
    finally:
        for task in list(running):
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


def create_app(
    *,
    engine: Any,
    jobs: JobStore,
    api_token: str | None = None,
    read_token: str | None = None,
    readiness_probe: Callable[[], Awaitable[ReadinessReport]] | None = None,
    rate_limit_per_minute: int = 0,
    rate_limiter: Any | None = None,
    job_timeout_seconds: float = 0,
    job_stale_seconds: float = 960,
    job_poll_seconds: float = 0.5,
    job_max_attempts: int = 3,
    max_concurrent_jobs: int = 1,
    max_queued_jobs: int = 16,
    max_active_jobs_per_caller: int = 4,
    allow_anonymous: bool = False,
    startup_hook: Callable[[], Awaitable[None]] | None = None,
    enable_docs: bool = False,
) -> FastAPI:
    """Build the FastAPI app around an engine + job store.

    Args:
        engine: Research engine facade (``ResearchEngine`` or a test double).
        jobs: Durable job store.
        api_token: Write token; data routes fail closed when it is unset.
        read_token: Optional read-only token accepted by read routes (sharing).
        readiness_probe: Optional readiness check; defaults to always-ok.
        rate_limit_per_minute: Per-key limit for the default in-process limiter.
        rate_limiter: Optional limiter override (e.g. ``PostgresRateLimiter``).
        job_timeout_seconds: Abort a run after this long (<= 0 disables).
        job_stale_seconds: Age after which a running claim is considered abandoned.
        job_poll_seconds: Worker idle poll interval.
        job_max_attempts: Attempts after which a recovered job is failed.
        max_concurrent_jobs: Maximum jobs executed at once.
        max_queued_jobs: Maximum queued jobs (<= 0 disables the cap).
        max_active_jobs_per_caller: Maximum queued+running jobs per caller
            (<= 0 disables the cap).
        allow_anonymous: Permit data routes when no token is configured. Local
            development only; production keeps this false.
        startup_hook: Optional async hook run before the engine starts.
        enable_docs: Serve Swagger/OpenAPI (set False in production).

    Returns:
        FastAPI: The configured application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Enter engine resources, recover jobs, and run the queue worker.

        Args:
            app: The application.

        Yields:
            None: Control while the app is serving.
        """
        if startup_hook is not None:
            await startup_hook()
        setup = getattr(engine, "setup", None)
        if setup is not None:
            await setup()
        ensure = getattr(jobs, "ensure_schema", None)
        if ensure is not None:
            await ensure()
        ensure_limiter = getattr(limiter, "ensure_schema", None)
        if ensure_limiter is not None:
            try:
                await asyncio.to_thread(ensure_limiter)
            except Exception:  # noqa: BLE001 - the shared limiter fails open at request time
                logger.exception("rate limiter schema setup failed")
        try:
            recovered = await jobs.recover_stale(stale_after_seconds=job_stale_seconds, max_attempts=job_max_attempts)
            if recovered:
                logger.warning("recovered %d stale job(s) on startup", recovered)
        except Exception:  # noqa: BLE001 - readiness will surface a broken store
            logger.exception("startup job recovery failed")
        stop = asyncio.Event()
        worker = asyncio.create_task(
            _worker_loop(
                engine,
                jobs,
                max_concurrent=max(1, max_concurrent_jobs),
                timeout_seconds=job_timeout_seconds,
                poll_seconds=max(_POLL_FLOOR_SECONDS, job_poll_seconds),
                stale_seconds=job_stale_seconds,
                max_attempts=job_max_attempts,
                stop=stop,
            ),
            name="service-worker",
        )
        try:
            yield
        finally:
            stop.set()
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            close = getattr(engine, "close", None)
            if close is not None:
                await close()

    app = FastAPI(
        title="Knowledge Engine",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
    )
    app.state.engine = engine
    app.state.jobs = jobs
    limiter = rate_limiter if rate_limiter is not None else RateLimiter(rate_limit_per_minute)

    async def _check(authorization: str | None, allowed: tuple[str | None, ...]) -> None:
        """Reject a request whose bearer token is not in ``allowed``.

        Args:
            authorization: The ``Authorization`` header value.
            allowed: Accepted tokens (``None`` entries are ignored).

        Raises:
            HTTPException: 401 when the token is missing or wrong, 503 when the
                server has no auth configured and anonymous access is disabled.
        """
        valid = [token for token in allowed if token]
        if not valid:
            if allow_anonymous:
                return
            raise HTTPException(status_code=503, detail="authentication is not configured")
        if authorization is None:
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")
        provided = authorization.encode("utf-8")
        if any(hmac.compare_digest(provided, f"Bearer {token}".encode()) for token in valid):
            return
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        """Require the write token (mutating routes).

        Args:
            authorization: The ``Authorization`` header value.
        """
        await _check(authorization, (api_token,))

    async def require_read_token(authorization: str | None = Header(default=None)) -> None:
        """Require a read token (the write token or the read-only token).

        Args:
            authorization: The ``Authorization`` header value.
        """
        await _check(authorization, (api_token, read_token))

    async def throttle(request: Request) -> None:
        """Reject requests over the per-key rate limit.

        Args:
            request: The incoming request (keyed on the hashed bearer token or host).

        Raises:
            HTTPException: 429 when the caller exceeds the limit.
        """
        if not limiter.enabled:
            return
        key = _caller_id(request)
        try:
            allowed = await asyncio.to_thread(limiter.allow, key)
        except Exception:  # noqa: BLE001 - a limiter outage must not block work
            logger.exception("rate limiter failed open")
            return
        if not allowed:
            try:
                retry = await asyncio.to_thread(limiter.retry_after, key)
            except Exception:  # noqa: BLE001 - Retry-After is advisory
                retry = 60.0
            retry_after = str(max(1, math.ceil(retry)))
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": retry_after},
            )

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
        """Readiness: DB, auth, migrations, storage, and providers.

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
    async def research(request: Request, req: ResearchRequest) -> ResearchAccepted:
        """Queue a research run and return a job id (202).

        Args:
            request: Incoming request (caller identity for quotas).
            req: The research request.

        Returns:
            ResearchAccepted: The accepted job id + status.

        Raises:
            HTTPException: 429 when the queue or the caller's active-job cap is full.
        """
        caller = _caller_id(request)
        if max_queued_jobs > 0 and await jobs.count_queued() >= max_queued_jobs:
            raise HTTPException(
                status_code=429,
                detail="job queue is full",
                headers={"Retry-After": "30"},
            )
        if max_active_jobs_per_caller > 0 and await jobs.count_active(caller=caller) >= max_active_jobs_per_caller:
            raise HTTPException(
                status_code=429,
                detail="caller job limit reached",
                headers={"Retry-After": "30"},
            )
        task_id = uuid.uuid4().hex
        record = JobRecord(
            task_id=task_id,
            status=JobStatus.QUEUED,
            thread_id=req.thread_id or task_id,
            query=req.query,
            workflow=req.workflow,
            settings=req.settings.model_dump(mode="json", exclude_none=True) if req.settings else None,
            caller=caller,
        )
        await jobs.create(record)
        return ResearchAccepted(task_id=task_id, status=record.status)

    @app.get(
        "/result/{task_id}", response_model=JobRecord, dependencies=[Depends(require_read_token), Depends(throttle)]
    )
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

    @app.get(
        "/result/{task_id}/artifact",
        dependencies=[Depends(require_read_token), Depends(throttle)],
    )
    async def result_artifact(
        task_id: str,
        format: Literal["markdown", "json"] = Query(default="markdown"),
    ) -> Any:
        """Return a completed job's rendered artifact.

        Args:
            task_id: Job id.
            format: ``markdown`` (default) or ``json`` (article + sources).

        Returns:
            Any: A Markdown response or a JSON mapping.

        Raises:
            HTTPException: 404 for an unknown task/missing artifact, 409 when
                the job has not completed.
        """
        record = await jobs.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown task id")
        if record.status != JobStatus.DONE:
            raise HTTPException(status_code=409, detail=f"job is {record.status.value}, not done")
        artifacts = _artifact_store(engine)
        if artifacts is None:
            raise HTTPException(status_code=404, detail="artifact store is not available")
        article = await asyncio.to_thread(artifacts.get_article, record.thread_id)
        if article is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        if format == "markdown":
            return PlainTextResponse(article, media_type="text/markdown")
        sources = await asyncio.to_thread(artifacts.get_sources, record.thread_id)
        output_path = (record.result or {}).get("output_path")
        return {
            "task_id": record.task_id,
            "thread_id": record.thread_id,
            "article": article,
            "sources": [source.model_dump(mode="json") for source in sources],
            "output_path": output_path,
        }

    @app.get(
        "/result/{task_id}/sources",
        dependencies=[Depends(require_read_token), Depends(throttle)],
    )
    async def result_sources(task_id: str) -> dict[str, Any]:
        """Return a completed job's persisted sources.

        Args:
            task_id: Job id.

        Returns:
            dict[str, Any]: ``{"task_id", "thread_id", "sources"}``.

        Raises:
            HTTPException: 404 for an unknown task, 409 when not done.
        """
        record = await jobs.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown task id")
        if record.status != JobStatus.DONE:
            raise HTTPException(status_code=409, detail=f"job is {record.status.value}, not done")
        artifacts = _artifact_store(engine)
        if artifacts is None:
            raise HTTPException(status_code=404, detail="artifact store is not available")
        sources = await asyncio.to_thread(artifacts.get_sources, record.thread_id)
        return {
            "task_id": record.task_id,
            "thread_id": record.thread_id,
            "sources": [source.model_dump(mode="json") for source in sources],
        }

    @app.get("/wiki/query", dependencies=[Depends(require_read_token), Depends(throttle)])
    async def wiki_query(
        q: str = Query(..., min_length=1, max_length=2000),
        limit: int = Query(default=3, ge=1, le=20),
    ) -> dict[str, Any]:
        """Answer from the compiled wiki with no live call (token-gated).

        Args:
            q: Query text.
            limit: Maximum hits.

        Returns:
            dict[str, Any]: ``{"hits": [...]}`` without server-local paths.
        """
        hits = await engine.wiki_query(q, limit=limit)
        return {"hits": [hit.model_dump(exclude={"path"}) for hit in hits]}

    @app.get("/wiki/page/{slug}", dependencies=[Depends(require_read_token), Depends(throttle)])
    async def wiki_page(slug: str) -> PlainTextResponse:
        """Read one compiled wiki page as Markdown.

        Args:
            slug: Kebab-case page slug.

        Returns:
            PlainTextResponse: The page Markdown.

        Raises:
            HTTPException: 404 when the page does not exist, 422 for a bad slug.
        """
        try:
            validate_wiki_slug(slug)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        text = await engine.wiki_page(slug)
        if text is None:
            raise HTTPException(status_code=404, detail="wiki page not found")
        return PlainTextResponse(text, media_type="text/markdown")

    @app.post("/wiki/ingest", dependencies=[Depends(require_token), Depends(throttle)])
    async def wiki_ingest(req: WikiIngestRequest) -> dict[str, Any]:
        """Re-ingest a raw run into the wiki.

        Args:
            req: The ingest request.

        Returns:
            dict[str, Any]: The ingest result.

        Raises:
            HTTPException: 404 when the referenced article does not exist.
        """
        article_path = req.article_path
        temp_path: Path | None = None
        if article_path is None:
            artifacts = _artifact_store(engine)
            if artifacts is None:
                raise HTTPException(status_code=404, detail="artifact store is not available")
            article = await asyncio.to_thread(artifacts.get_article, req.thread_id)
            if article is None:
                raise HTTPException(status_code=404, detail="artifact for this thread was not found")
            root = Path(getattr(engine, "store_root", "research_store"))
            ingest_dir = root / "_api_ingest"
            ingest_dir.mkdir(parents=True, exist_ok=True)
            temp_path = ingest_dir / f"{uuid.uuid4().hex}.md"
            temp_path.write_text(article, encoding="utf-8")
            article_path = str(temp_path)
        try:
            ingested = await engine.wiki_ingest(req.thread_id, req.query, article_path)
        except WikiArticleMissing as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
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

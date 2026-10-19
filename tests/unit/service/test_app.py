"""Unit tests: the HTTP service app (offline, fake engine + in-memory jobs).

Verifies the #30 API surface end to end without a network or cloud: health,
readiness, the 202→poll research flow, bearer auth on data routes, the queue
worker (recovery + concurrency caps), and the wiki query/ingest/lint routes.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from knowledge_engine.schemas.wiki import IngestResult, LintReport, WikiVerdict
from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import InMemoryJobStore
from knowledge_engine.service.schemas import JobRecord, JobStatus, ReadinessReport
from knowledge_engine.store.research_store import ResearchStore


class FakeEngine:
    """Engine double: records calls and returns canned summaries."""

    def __init__(self) -> None:
        self.setup_called = False
        self.closed = False
        self.ran: list[tuple[str, str]] = []
        self.settings_seen: list[Any] = []
        self.ingested_paths: list[str] = []

    async def setup(self) -> None:
        """Mark startup."""
        self.setup_called = True

    async def close(self) -> None:
        """Mark shutdown."""
        self.closed = True

    async def research(
        self,
        query: str,
        *,
        workflow: str = "research",
        thread_id: str | None = None,
        settings: Any = None,
        **_: Any,
    ) -> Any:
        """Return a done result summarizing the run.

        Args:
            query: Query text.
            workflow: Workflow name.
            thread_id: Thread id.
            settings: Validated per-run settings (recorded).
            **_: Ignored extra kwargs.

        Returns:
            Any: A RunResult-like object.
        """
        self.ran.append((query, workflow))
        self.settings_seen.append(settings)
        state = SimpleNamespace(status=SimpleNamespace(value="done"), output_path="/tmp/a.md", sources=[1, 2, 3])
        return SimpleNamespace(state=state)

    async def wiki_query(self, q: str, *, limit: int = 3) -> list[Any]:
        """Return no hits.

        Args:
            q: Query.
            limit: Max hits.

        Returns:
            list[Any]: Empty list.
        """
        return []

    async def wiki_ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """Return a canned NEW verdict.

        Args:
            thread_id: Thread id.
            query: Query.
            article_path: Article path.

        Returns:
            IngestResult: Canned result.
        """
        self.ingested_paths.append(article_path)
        return IngestResult(verdict=WikiVerdict.NEW, page_path="sources/x.md", pages_touched=1)

    async def wiki_page(self, slug: str) -> str | None:
        """Return a canned wiki page.

        Args:
            slug: Page slug.

        Returns:
            str | None: Page markdown, or None for ``missing``.
        """
        return None if slug == "missing" else f"# {slug}"

    async def wiki_lint(self) -> LintReport:
        """Return a clean report.

        Returns:
            LintReport: Clean report.
        """
        return LintReport(root="wiki")


def _client(
    *, api_token: str | None = None, readiness: ReadinessReport | None = None
) -> tuple[TestClient, FakeEngine]:
    """Build a TestClient over the app with fakes.

    Args:
        api_token: Optional bearer token to require.
        readiness: Optional readiness probe result.

    Returns:
        tuple[TestClient, FakeEngine]: The client and its engine double.
    """
    engine = FakeEngine()

    async def probe() -> ReadinessReport:
        return readiness or ReadinessReport(ok=True, checks={"database": True})

    app = create_app(
        engine=engine,
        jobs=InMemoryJobStore(),
        api_token=api_token,
        readiness_probe=probe,
        allow_anonymous=api_token is None,
        job_poll_seconds=0.01,
    )
    return TestClient(app), engine


def _wait_status(
    client: TestClient,
    task_id: str,
    expected: set[str],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll ``/result`` until the job reaches one of ``expected``.

    Args:
        client: The test client.
        task_id: Job id.
        expected: Accepted terminal or intermediate statuses.
        headers: Optional auth headers.
        timeout: Maximum seconds to wait.

    Returns:
        dict[str, Any]: The matching job body.

    Raises:
        AssertionError: When the timeout elapses first.
    """
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/result/{task_id}", headers=headers).json()
        if body.get("status") in expected:
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {task_id} not in {expected}: last={body}")


def test_health_is_open() -> None:
    """GET /health needs no auth and reports ok."""
    client, _ = _client(api_token="secret")
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readiness_reports_checks() -> None:
    """GET /readiness returns the injected probe result."""
    report = ReadinessReport(ok=True, checks={"database": True, "storage": True})
    client, _ = _client(readiness=report)
    resp = client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "checks": {"database": True, "storage": True}}


def test_research_returns_202_and_completes() -> None:
    """POST /research returns 202, the worker runs the job, and /result shows done."""
    client, engine = _client()
    with client:
        resp = client.post("/research", json={"query": "what is rag", "workflow": "qa"})
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]
        assert resp.json()["status"] == "queued"
        body = _wait_status(client, task_id, {"done"})
    assert body["result"]["sources"] == 3
    assert engine.ran == [("what is rag", "qa")]


def test_data_routes_fail_closed_without_token() -> None:
    """With no token and anonymous mode off, data routes refuse with 503."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore())
    client = TestClient(app)
    assert client.post("/research", json={"query": "q"}).status_code == 503
    assert client.get("/wiki/query", params={"q": "x"}).status_code == 503


def test_mutating_routes_require_bearer_token() -> None:
    """Without/with a wrong token POST /research is 401; the right token passes."""
    client, _ = _client(api_token="secret")
    assert client.post("/research", json={"query": "q"}).status_code == 401
    assert client.post("/research", json={"query": "q"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post("/research", json={"query": "q"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 202


def test_result_unknown_task_is_404() -> None:
    """GET /result for an unknown id is 404."""
    client, _ = _client()
    assert client.get("/result/nope").status_code == 404


def test_rate_limiter_returns_429_when_exceeded() -> None:
    """A second request over a per-minute limit of 1 is refused with 429."""
    engine = FakeEngine()
    app = create_app(
        engine=engine,
        jobs=InMemoryJobStore(),
        rate_limit_per_minute=1,
        allow_anonymous=True,
        job_poll_seconds=0.01,
    )
    client = TestClient(app)
    assert client.post("/research", json={"query": "a"}).status_code == 202
    assert client.post("/research", json={"query": "b"}).status_code == 429


def test_wiki_query_requires_token() -> None:
    """GET /wiki/query is gated by the bearer token (accumulated knowledge is not public)."""
    client, _ = _client(api_token="secret")
    assert client.get("/wiki/query", params={"q": "rag"}).status_code == 401
    ok = client.get("/wiki/query", params={"q": "rag"}, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert ok.json() == {"hits": []}


def test_wiki_ingest_and_lint_require_token() -> None:
    """Wiki mutation routes honour the bearer token and return the adapter payloads."""
    client, _ = _client(api_token="secret")
    body = {"thread_id": "t", "query": "q", "article_path": "a.md"}
    assert client.post("/wiki/ingest", json=body).status_code == 401
    ok = client.post("/wiki/ingest", json=body, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert ok.json()["verdict"] == "new"
    lint = client.post("/wiki/lint", headers={"Authorization": "Bearer secret"})
    assert lint.status_code == 200
    assert lint.json()["root"] == "wiki"
    assert lint.json()["ok"] is True


def test_read_only_token_scope() -> None:
    """A read token can read the wiki but cannot start a run."""
    engine = FakeEngine()
    app = create_app(
        engine=engine,
        jobs=InMemoryJobStore(),
        api_token="write",
        read_token="read",
        job_poll_seconds=0.01,
    )
    client = TestClient(app)
    assert client.get("/wiki/query", params={"q": "x"}, headers={"Authorization": "Bearer read"}).status_code == 200
    assert client.get("/wiki/query", params={"q": "x"}, headers={"Authorization": "Bearer write"}).status_code == 200
    assert client.post("/research", json={"query": "q"}, headers={"Authorization": "Bearer read"}).status_code == 401
    assert client.post("/research", json={"query": "q"}, headers={"Authorization": "Bearer write"}).status_code == 202


def test_result_requires_read_token_when_configured() -> None:
    """GET /result is gated by the read token when tokens are set."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore(), api_token="write", read_token="read")
    client = TestClient(app)
    assert client.get("/result/nope").status_code == 401
    assert client.get("/result/nope", headers={"Authorization": "Bearer read"}).status_code == 404


async def test_job_timeout_marks_error() -> None:
    """A run exceeding the timeout is marked error, never left running."""

    class SlowEngine(FakeEngine):
        async def research(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(1.0)
            return await super().research(*args, **kwargs)

    app = create_app(
        engine=SlowEngine(),
        jobs=InMemoryJobStore(),
        job_timeout_seconds=0.05,
        allow_anonymous=True,
        job_poll_seconds=0.01,
    )
    client = TestClient(app)
    with client:
        accepted = client.post("/research", json={"query": "slow"})
        assert accepted.status_code == 202
        body = _wait_status(client, accepted.json()["task_id"], {"error"})
    assert "timed out" in (body["error"] or "")


def test_worker_resumes_queued_job_on_startup() -> None:
    """A job already queued when the app starts is picked up by the worker."""
    jobs = InMemoryJobStore()
    record = JobRecord(task_id="resume-me", status=JobStatus.QUEUED, thread_id="resume-me", query="q", workflow="qa")
    asyncio.run(jobs.create(record))
    engine = FakeEngine()
    app = create_app(engine=engine, jobs=jobs, allow_anonymous=True, job_poll_seconds=0.01)
    with TestClient(app) as client:
        body = _wait_status(client, "resume-me", {"done"})
    assert body["result"]["sources"] == 3
    assert engine.ran == [("q", "qa")]


def test_stale_running_job_is_requeued_on_startup() -> None:
    """A crashed worker's running job is requeued (never stuck running)."""
    jobs = InMemoryJobStore()
    record = JobRecord(task_id="stale", status=JobStatus.RUNNING, thread_id="stale", query="q", workflow="qa")
    asyncio.run(jobs.create(record))
    app = create_app(engine=FakeEngine(), jobs=jobs, allow_anonymous=True, job_poll_seconds=0.01)
    with TestClient(app) as client:
        body = _wait_status(client, "stale", {"done"})
    assert body["result"]["sources"] == 3


def test_worker_respects_max_concurrent_jobs() -> None:
    """The worker never runs more jobs at once than ``max_concurrent_jobs``."""

    class ConcurrencyEngine(FakeEngine):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def research(self, *args: Any, **kwargs: Any) -> Any:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.05)
                return await super().research(*args, **kwargs)
            finally:
                self.active -= 1

    engine = ConcurrencyEngine()
    app = create_app(
        engine=engine,
        jobs=InMemoryJobStore(),
        allow_anonymous=True,
        max_concurrent_jobs=1,
        job_poll_seconds=0.01,
    )
    with TestClient(app) as client:
        task_ids = [client.post("/research", json={"query": f"q{i}"}).json()["task_id"] for i in range(3)]
        for task_id in task_ids:
            _wait_status(client, task_id, {"done"})
    assert engine.max_active == 1


def test_per_caller_active_cap_returns_429() -> None:
    """A caller at its active-job cap gets 429 instead of unbounded queueing."""
    app = create_app(
        engine=FakeEngine(),
        jobs=InMemoryJobStore(),
        allow_anonymous=True,
        max_active_jobs_per_caller=1,
    )
    client = TestClient(app)
    assert client.post("/research", json={"query": "a"}).status_code == 202
    refused = client.post("/research", json={"query": "b"})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1


def test_queue_depth_cap_returns_429() -> None:
    """A full queue refuses new jobs with 429 instead of queueing forever."""
    app = create_app(
        engine=FakeEngine(),
        jobs=InMemoryJobStore(),
        allow_anonymous=True,
        max_queued_jobs=1,
    )
    client = TestClient(app)
    assert client.post("/research", json={"query": "a"}).status_code == 202
    assert client.post("/research", json={"query": "b"}).status_code == 429


def test_docs_can_be_disabled() -> None:
    """enable_docs=False removes Swagger/OpenAPI from the app."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore(), enable_docs=False)
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/health").status_code == 200


def test_docs_are_disabled_by_default() -> None:
    """The factory defaults to no Swagger/OpenAPI (production posture)."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore())
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_lifespan_runs_setup_and_close() -> None:
    """Entering/exiting the app lifespan calls engine.setup()/close()."""
    engine = FakeEngine()
    app = create_app(engine=engine, jobs=InMemoryJobStore(), allow_anonymous=True)
    with TestClient(app):
        assert engine.setup_called is True
    assert engine.closed is True


@pytest.mark.parametrize("workflow", ["research", "qa", "summary", "deep"])
def test_research_accepts_every_workflow(workflow: str) -> None:
    """All four workflows are accepted (202) and executed by the worker."""
    client, engine = _client()
    with client:
        resp = client.post("/research", json={"query": "q", "workflow": workflow})
        assert resp.status_code == 202
        _wait_status(client, resp.json()["task_id"], {"done"})
    assert engine.ran[-1] == ("q", workflow)


def test_research_rejects_unknown_workflow_with_422() -> None:
    """An unknown workflow is a validation error at submit, not a dead job."""
    client, engine = _client()
    resp = client.post("/research", json={"query": "q", "workflow": "bogus"})
    assert resp.status_code == 422
    assert engine.ran == []


@pytest.mark.parametrize("query", ["", "   "])
def test_research_rejects_blank_query_with_422(query: str) -> None:
    """Blank and whitespace-only queries are rejected before a job exists."""
    client, engine = _client()
    resp = client.post("/research", json={"query": query})
    assert resp.status_code == 422
    assert engine.ran == []


@pytest.mark.parametrize("settings", [{"max_depth": -1}, {"max_total_tokens": "many"}, {"unknown_knob": 1}])
def test_research_rejects_invalid_settings_with_422(settings: dict[str, Any]) -> None:
    """Invalid per-run settings fail at submit with 422, not inside the job."""
    client, engine = _client()
    resp = client.post("/research", json={"query": "q", "settings": settings})
    assert resp.status_code == 422
    assert engine.ran == []


def test_research_passes_validated_settings_to_engine() -> None:
    """Valid settings are persisted with the job and reach the engine."""
    from knowledge_engine.schemas.settings import RunSettings

    client, engine = _client()
    with client:
        resp = client.post("/research", json={"query": "q", "settings": {"max_subqueries": 3}})
        assert resp.status_code == 202
        _wait_status(client, resp.json()["task_id"], {"done"})
    assert engine.settings_seen[-1] == RunSettings(max_subqueries=3)


def test_rate_limited_response_carries_retry_after() -> None:
    """A 429 advertises when the caller may retry (audit defect: no header)."""
    app = create_app(
        engine=FakeEngine(),
        jobs=InMemoryJobStore(),
        rate_limit_per_minute=1,
        allow_anonymous=True,
    )
    client = TestClient(app)
    assert client.post("/research", json={"query": "a"}).status_code == 202
    refused = client.post("/research", json={"query": "b"})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1


def test_wiki_ingest_missing_article_is_404() -> None:
    """A missing article path is a client error, not a 500 (audit defect)."""
    from knowledge_engine.core.exceptions import WikiArticleMissing

    class MissingArticleEngine(FakeEngine):
        async def wiki_ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
            raise WikiArticleMissing(f"supabase wiki ingest: article missing: {article_path}")

    app = create_app(engine=MissingArticleEngine(), jobs=InMemoryJobStore(), allow_anonymous=True)
    client = TestClient(app)
    body = {"thread_id": "t", "query": "q", "article_path": "nowhere.md"}
    resp = client.post("/wiki/ingest", json=body)
    assert resp.status_code == 404
    assert "article missing" in resp.json()["detail"]


async def _seed_done_job(jobs: InMemoryJobStore, task_id: str, thread_id: str) -> None:
    """Insert a completed job for artifact-endpoint tests.

    Args:
        jobs: In-memory store.
        task_id: Job id.
        thread_id: Thread id.
    """
    await jobs.create(JobRecord(task_id=task_id, status=JobStatus.DONE, thread_id=thread_id, query="q", workflow="qa"))


def _artifact_client(tmp_path: Path) -> tuple[TestClient, FakeEngine, InMemoryJobStore]:
    """Build an app whose engine exposes a local artifact store.

    Args:
        tmp_path: Pytest temp directory.

    Returns:
        tuple[TestClient, FakeEngine, InMemoryJobStore]: Client, engine, jobs.
    """
    engine = FakeEngine()
    engine.store = ResearchStore(tmp_path)  # type: ignore[attr-defined]
    engine.store_root = str(tmp_path)  # type: ignore[attr-defined]
    jobs = InMemoryJobStore()
    app = create_app(engine=engine, jobs=jobs, allow_anonymous=True, job_poll_seconds=0.01)
    return TestClient(app), engine, jobs


def test_artifact_markdown_and_json(tmp_path: Path) -> None:
    """A completed job serves its article as Markdown or JSON."""
    client, engine, jobs = _artifact_client(tmp_path)
    asyncio.run(_seed_done_job(jobs, "a1", "t1"))
    engine.store.article_path("t1").write_text("# hello\n", encoding="utf-8")
    engine.store.sources_path("t1").write_text("[]", encoding="utf-8")

    markdown = client.get("/result/a1/artifact")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.text == "# hello\n"

    payload = client.get("/result/a1/artifact", params={"format": "json"}).json()
    assert payload["task_id"] == "a1"
    assert payload["article"] == "# hello\n"
    assert payload["sources"] == []


def test_artifact_endpoint_errors(tmp_path: Path) -> None:
    """Unknown, unfinished, and missing-artifact cases are explicit."""
    client, _engine, jobs = _artifact_client(tmp_path)
    assert client.get("/result/nope/artifact").status_code == 404
    asyncio.run(
        jobs.create(JobRecord(task_id="q1", status=JobStatus.QUEUED, thread_id="t1", query="q", workflow="qa"))
    )
    assert client.get("/result/q1/artifact").status_code == 409
    asyncio.run(_seed_done_job(jobs, "d1", "no-artifact"))
    assert client.get("/result/d1/artifact").status_code == 404


def test_sources_endpoint(tmp_path: Path) -> None:
    """Persisted sources are available without the article payload."""
    client, engine, jobs = _artifact_client(tmp_path)
    asyncio.run(_seed_done_job(jobs, "a1", "t1"))
    engine.store.sources_path("t1").write_text("[]", encoding="utf-8")
    body = client.get("/result/a1/sources").json()
    assert body == {"task_id": "a1", "thread_id": "t1", "sources": []}


def test_wiki_page_read_and_validation() -> None:
    """Wiki page reads are Markdown; bad slugs and misses are handled."""
    client, _ = _client()
    ok = client.get("/wiki/page/retrieval-augmented-generation")
    assert ok.status_code == 200
    assert ok.text == "# retrieval-augmented-generation"
    assert client.get("/wiki/page/missing").status_code == 404
    assert client.get("/wiki/page/Bad_Slug").status_code == 422


def test_wiki_ingest_resolves_artifact_by_thread(tmp_path: Path) -> None:
    """Remote ingest loads the artifact and cleans up its temp file."""
    client, engine, _jobs = _artifact_client(tmp_path)
    engine.store.article_path("t1").write_text("# ingest me\n", encoding="utf-8")
    resp = client.post("/wiki/ingest", json={"thread_id": "t1", "query": "q"})
    assert resp.status_code == 200
    path = engine.ingested_paths[-1]
    assert "_api_ingest" in path
    assert not Path(path).exists()
    assert client.post("/wiki/ingest", json={"thread_id": "missing", "query": "q"}).status_code == 404


def test_request_bounds_and_thread_safety() -> None:
    """Unsafe ids and budget-busting settings are rejected at submit time."""
    client, _ = _client()
    assert client.post("/research", json={"query": "q", "thread_id": "../x"}).status_code == 422
    assert client.post("/research", json={"query": "q", "settings": {"max_subqueries": 8}}).status_code == 422
    assert client.get("/wiki/query", params={"q": "x", "limit": 0}).status_code == 422
    assert client.get("/wiki/query", params={"q": "x" * 2001}).status_code == 422
    assert client.post("/wiki/ingest", json={"thread_id": "../x", "query": "q"}).status_code == 422

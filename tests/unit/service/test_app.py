"""Unit tests: the HTTP service app (offline, fake engine + in-memory jobs).

Verifies the #30 API surface end to end without a network or cloud: health,
readiness, the 202→poll research flow, bearer auth on mutating routes, and the
wiki query/ingest/lint routes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from knowledge_engine.schemas.wiki import IngestResult, LintReport, WikiVerdict
from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import InMemoryJobStore
from knowledge_engine.service.schemas import ReadinessReport


class FakeEngine:
    """Engine double: records calls and returns canned summaries."""

    def __init__(self) -> None:
        self.setup_called = False
        self.closed = False
        self.ran: list[tuple[str, str]] = []
        self.settings_seen: list[Any] = []

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
        return IngestResult(verdict=WikiVerdict.NEW, page_path="sources/x.md", pages_touched=1)

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

    app = create_app(engine=engine, jobs=InMemoryJobStore(), api_token=api_token, readiness_probe=probe)
    return TestClient(app), engine


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
    """POST /research returns 202, runs the job, and GET /result shows done."""
    client, engine = _client()
    resp = client.post("/research", json={"query": "what is rag", "workflow": "qa"})
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    assert resp.json()["status"] == "queued"
    polled = client.get(f"/result/{task_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "done"
    assert body["result"]["sources"] == 3
    assert engine.ran == [("what is rag", "qa")]


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
    app = create_app(engine=engine, jobs=InMemoryJobStore(), rate_limit_per_minute=1)
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
    app = create_app(engine=engine, jobs=InMemoryJobStore(), api_token="write", read_token="read")
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
    import asyncio

    class SlowEngine(FakeEngine):
        async def research(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(1.0)
            return await super().research(*args, **kwargs)

    app = create_app(engine=SlowEngine(), jobs=InMemoryJobStore(), job_timeout_seconds=0.05)
    client = TestClient(app)
    accepted = client.post("/research", json={"query": "slow"})
    assert accepted.status_code == 202
    body = client.get(f"/result/{accepted.json()['task_id']}").json()
    assert body["status"] == "error"
    assert "timed out" in (body["error"] or "")


def test_docs_can_be_disabled() -> None:
    """enable_docs=False removes Swagger/OpenAPI from the app."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore(), enable_docs=False)
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/health").status_code == 200


def test_lifespan_runs_setup_and_close() -> None:
    """Entering/exiting the app lifespan calls engine.setup()/close()."""
    engine = FakeEngine()
    app = create_app(engine=engine, jobs=InMemoryJobStore())
    with TestClient(app):
        assert engine.setup_called is True
    assert engine.closed is True


@pytest.mark.parametrize("workflow", ["research", "qa", "summary", "deep"])
def test_research_accepts_every_workflow(workflow: str) -> None:
    """All four workflows are accepted (202) and recorded."""
    client, engine = _client()
    resp = client.post("/research", json={"query": "q", "workflow": workflow})
    assert resp.status_code == 202
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
    """Valid settings reach the engine as a validated RunSettings instance."""
    from knowledge_engine.schemas.settings import RunSettings

    client, engine = _client()
    resp = client.post("/research", json={"query": "q", "settings": {"max_subqueries": 3}})
    assert resp.status_code == 202
    assert engine.settings_seen[-1] == RunSettings(max_subqueries=3)


def test_rate_limited_response_carries_retry_after() -> None:
    """A 429 advertises when the caller may retry (audit defect: no header)."""
    app = create_app(engine=FakeEngine(), jobs=InMemoryJobStore(), rate_limit_per_minute=1)
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

    app = create_app(engine=MissingArticleEngine(), jobs=InMemoryJobStore())
    client = TestClient(app)
    body = {"thread_id": "t", "query": "q", "article_path": "nowhere.md"}
    resp = client.post("/wiki/ingest", json=body)
    assert resp.status_code == 404
    assert "article missing" in resp.json()["detail"]

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

    async def setup(self) -> None:
        """Mark startup."""
        self.setup_called = True

    async def close(self) -> None:
        """Mark shutdown."""
        self.closed = True

    async def research(self, query: str, *, workflow: str = "research", thread_id: str | None = None, **_: Any) -> Any:
        """Return a done result summarizing the run.

        Args:
            query: Query text.
            workflow: Workflow name.
            thread_id: Thread id.
            **_: Ignored extra kwargs (settings).

        Returns:
            Any: A RunResult-like object.
        """
        self.ran.append((query, workflow))
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

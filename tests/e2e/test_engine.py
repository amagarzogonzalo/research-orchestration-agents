"""E2E: ResearchEngine facade — files-only research run + resume + discovery surface."""

from __future__ import annotations

from knowledge_engine.core.discovery import FreeModel, LLMDiscoveryPort, TestResult
from knowledge_engine.engine import ResearchEngine
from knowledge_engine.infrastructure.discovery.service import ModelDiscoverer
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


def _engine(tmp_path, *, interactive_review: bool = False) -> ResearchEngine:
    """Build a ResearchEngine wired to fakes + a tmp store."""
    store_root = str(tmp_path / "store")
    engine = ResearchEngine(store_root=store_root, auto_approve_review=not interactive_review)
    engine._search = FakeSearch()
    engine._scrape = FakeScrape()
    engine._provider = FakeProvider(judge_pass=True)
    engine._discoverer = ModelDiscoverer(FakeDiscoveryPass())
    return engine


class FakeDiscoveryPass(LLMDiscoveryPort):
    async def list_free_models(self) -> list[FreeModel]:
        return [FreeModel(model_id="fake-free", provider="Fake", context_length=10)]

    async def test(self, model_id: str) -> TestResult:
        return TestResult(model_id, True, "ok")

    async def complete(self, model_id: str, prompt: str) -> str:
        return f"[{model_id}] {prompt}"


async def test_engine_research_writes_files(tmp_path) -> None:
    engine = _engine(tmp_path)
    result = await engine.research("multi-agent orchestration", thread_id="e2e-1")
    assert result.state.status.value == "done"
    # Files-only persistence: metadata + sources + events exist under the store.
    assert engine.store.metadata_path("e2e-1").exists()
    assert engine.store.sources_path("e2e-1").exists()
    assert engine.store.events_path("e2e-1").exists()
    assert result.state.output_path


async def test_engine_research_hitl_interrupt_then_resume(tmp_path) -> None:
    engine = _engine(tmp_path, interactive_review=True)
    first = await engine.research("human in the loop", thread_id="e2e-hitl")
    assert first.interrupted
    resumed = await engine.resume("research", "e2e-hitl", {"approved": True, "feedback": "ok"})
    assert not resumed.interrupted
    assert resumed.state.status.value == "done"


async def test_engine_discovers_free_model(tmp_path) -> None:
    engine = _engine(tmp_path)
    engine._discoverer = ModelDiscoverer(FakeDiscoveryPass())
    model = await engine.discover()
    assert model == "fake-free"


async def test_engine_search_and_scrape(tmp_path) -> None:
    engine = _engine(tmp_path)
    sources = await engine.search_run("agents")
    assert sources and sources[0].id == "openalex:W1"
    body = await engine.scrape_run("https://example.org/paper.pdf")
    assert body.markdown


async def test_engine_browser_requires_configuration(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        await engine.browser_act("navigate")
        raise AssertionError("expected RuntimeError for unconfigured browser")
    except RuntimeError as exc:
        assert "browser not configured" in str(exc)


async def test_engine_parallel_research_runs_stay_isolated(tmp_path) -> None:
    """N concurrent research() calls on one engine keep separate threads/files."""
    import asyncio

    engine = _engine(tmp_path)
    results = await asyncio.gather(*(engine.research(f"parallel query {i}", unique=True) for i in range(3)))
    tids = [r.state.thread_id for r in results]
    assert len(set(tids)) == 3, f"expected 3 distinct threads, got {tids}"
    assert all(r.state.status.value == "done" for r in results)
    for tid in tids:
        assert engine.store.metadata_path(tid).exists()
        assert engine.store.sources_path(tid).exists()


async def test_engine_default_thread_id_is_stable(tmp_path) -> None:
    """Without unique=True the same query maps to the same thread (HITL-safe)."""
    from knowledge_engine.engine import _derived_thread_id

    assert _derived_thread_id("same query") == _derived_thread_id("same query")
    assert _derived_thread_id("same query") != _derived_thread_id("other query")

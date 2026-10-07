"""Unit tests: free-LLM discovery service + fallback provider chain.

Uses a fake discovery provider so no network / real LLM is needed. The
FallbackDiscovery + ModelDiscoverer pipeline is verified offline.
"""

from __future__ import annotations

from knowledge_engine.core.discovery import (
    CompletionError,
    FreeModel,
    LLMDiscoveryPort,
    ModelId,
    TestResult,
)
from knowledge_engine.infrastructure.discovery.fallback_discovery import FallbackDiscovery
from knowledge_engine.infrastructure.discovery.service import ModelDiscoverer, ModelSelectionError
from knowledge_engine.infrastructure.discovery.text_utils import strip_reasoning_prefix


class FakeDiscovery(LLMDiscoveryPort):
    """Deterministic fake: all models pass except those in ``failing``."""

    def __init__(self, failing: set[ModelId] | None = None, models: list[FreeModel] | None = None) -> None:
        self.models = models or [
            FreeModel(model_id="big", provider="Fake", context_length=200_000),
            FreeModel(model_id="mid", provider="Fake", context_length=100_000),
        ]
        self.failing = failing or set()
        self.tests_calls = 0

    async def list_free_models(self) -> list[FreeModel]:
        return list(self.models)

    async def test(self, model_id: ModelId) -> TestResult:
        self.tests_calls += 1
        return TestResult(model_id, model_id not in self.failing, "ok" if model_id not in self.failing else "error")

    async def complete(self, model_id: ModelId, prompt: str) -> str:
        if model_id in self.failing:
            raise CompletionError(f"{model_id} failed")
        return f"[{model_id}] {prompt}"


async def test_discoverer_selects_first_working() -> None:
    provider = FakeDiscovery()
    discoverer = ModelDiscoverer(provider)
    model = await discoverer.select()
    assert model == "big"


async def test_discoverer_respects_preferred() -> None:
    provider = FakeDiscovery()
    discoverer = ModelDiscoverer(provider)
    model = await discoverer.select(preferred="mid")
    # Preferred is tried first and passes.
    assert model == "mid"


async def test_discoverer_preferred_skipped_when_failing() -> None:
    provider = FakeDiscovery(
        failing={"mid"},
        models=[
            FreeModel(model_id="mid", provider="Fake", context_length=1),
            FreeModel(model_id="big", provider="Fake", context_length=2),
        ],
    )
    discoverer = ModelDiscoverer(provider)
    model = await discoverer.select(preferred="mid")
    assert model == "big"


async def test_fresh_boot_never_reports_cooldown(monkeypatch) -> None:
    """Regression: with time.monotonic() under the cooldown window (a freshly
    booted runner/VM), models with no recorded failure must not read as
    cooling down — otherwise selection collapses to the preferred model and
    fails with 'tried all 1 candidates' (CI-only failure, unreproducible on
    long-uptime dev machines)."""
    import types

    import knowledge_engine.infrastructure.discovery.service as svc

    monkeypatch.setattr(svc, "time", types.SimpleNamespace(monotonic=lambda: 30.0))
    discoverer = ModelDiscoverer(FakeDiscovery(failing={"mid"}))
    assert discoverer._in_cooldown("mid") is False
    assert discoverer._in_cooldown("big") is False
    model = await discoverer.select(preferred="mid")
    assert model == "big"


async def test_discoverer_raises_when_all_fail() -> None:
    provider = FakeDiscovery(failing={"big", "mid"})
    discoverer = ModelDiscoverer(provider, max_attempts=5)
    try:
        await discoverer.select()
        raise AssertionError("expected ModelSelectionError")
    except ModelSelectionError as exc:
        assert "candidates" in str(exc) or "all" in str(exc)


async def test_discoverer_complete_uses_selected_model() -> None:
    provider = FakeDiscovery()
    discoverer = ModelDiscoverer(provider)
    out = await discoverer.complete("hello")
    assert out == "[big] hello"


async def test_fallback_discovery_aggregates_across_providers() -> None:
    a = FakeDiscovery()
    a.models = [FreeModel(model_id="a1", provider="A", context_length=10)]
    b = FakeDiscovery()
    b.models = [FreeModel(model_id="b1", provider="B", context_length=20)]
    chain = FallbackDiscovery([a, b])
    pool = await chain.list_free_models()
    ids = [m.model_id for m in pool]
    assert set(ids) == {"a1", "b1"}
    # FreeModel sorts by context length desc → b1 first.
    assert ids[0] == "b1"


async def test_fallback_routes_complete_to_owner() -> None:
    a = FakeDiscovery()
    a.models = [FreeModel(model_id="a1", provider="A", context_length=10)]
    b = FakeDiscovery()
    b.models = [FreeModel(model_id="b1", provider="B", context_length=20)]
    chain = FallbackDiscovery([a, b])
    out = await chain.complete("b1", "hi")
    assert out == "[b1] hi"


def test_strip_reasoning_prefix_removes_blocks() -> None:
    assert strip_reasoning_prefix("<thinking>hidden</thinking>answer") == "answer"
    assert strip_reasoning_prefix("plain answer") == "plain answer"


async def test_discoverer_from_settings_skips_empty_keys() -> None:
    discoverer = ModelDiscoverer.from_settings()  # no keys -> empty chain
    pool = await discoverer.free_models()
    assert pool == []


async def test_discoverer_empty_chain_raises() -> None:
    discoverer = ModelDiscoverer.from_settings()
    try:
        await discoverer.select()
        raise AssertionError("expected ModelSelectionError")
    except ModelSelectionError as exc:
        assert "no free" in str(exc)


async def test_discoverer_reuses_known_good_without_re_testing() -> None:
    """Repeated select() must reuse the validated model (no extra health checks)."""
    provider = FakeDiscovery()
    discoverer = ModelDiscoverer(provider)
    first = await discoverer.select()
    assert first == "big"
    # Second call hits the known-good fast path → no new test() calls.
    tests_before = provider.tests_calls
    second = await discoverer.select()
    assert second == "big"
    assert provider.tests_calls == tests_before
    # complete() also avoids re-discovery.
    out = await discoverer.complete("hi")
    assert out == "[big] hi"
    assert provider.tests_calls == tests_before

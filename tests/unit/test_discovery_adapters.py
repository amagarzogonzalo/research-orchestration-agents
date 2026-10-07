"""Unit tests: discovery HTTP adapters with stubbed transports (offline).

Covers the OpenAI-compatible base (ok / 429 / empty choices), the Gemini
REST mapping (ok / 429 / non-200 / empty content), and the OpenRouter catalog
scan (free-only filter, text->text modality, context-length ranking).
"""

from __future__ import annotations

import httpx
import pytest

from knowledge_engine.core.discovery import CompletionError, FreeModel
from knowledge_engine.infrastructure.discovery.gemini_discovery import GeminiDiscovery
from knowledge_engine.infrastructure.discovery.openai_compat import OpenAICompatDiscovery
from knowledge_engine.infrastructure.discovery.openrouter_catalog import OpenRouterCatalog


class StubCompat(OpenAICompatDiscovery):
    endpoint = "https://stub.test/v1"
    candidates: list[FreeModel] = [FreeModel(model_id="stub-1", provider="Stub", context_length=8_000)]

    def __init__(self, handler, **kwargs) -> None:
        super().__init__("fake-key", **kwargs)
        self._handler = handler

    async def _with_client(self, fn):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self._handler)) as client:
            return await fn(client)


def _chat_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "hello world, this is fine"}}]})


def _chat_429(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"error": "slow down"})


def _chat_empty(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": []})


async def test_compat_test_passes_on_good_reply() -> None:
    result = await StubCompat(_chat_ok).test("stub-1")
    assert (result.passed, result.reason) == (True, "ok")


async def test_compat_test_maps_429_to_rate_limited() -> None:
    result = await StubCompat(_chat_429).test("stub-1")
    assert (result.passed, result.reason) == (False, "rate_limited")


async def test_compat_test_maps_empty_choices_to_error() -> None:
    result = await StubCompat(_chat_empty).test("stub-1")
    assert result.passed is False
    assert result.reason == "no_choices"


async def test_compat_complete_returns_text() -> None:
    out = await StubCompat(_chat_ok).complete("stub-1", "hi")
    assert "hello world" in out


def _gemini_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "a proper sentence here yes"}]}}]})


def _gemini_429(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"error": "quota"})


def _gemini_empty(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"candidates": []})


_REAL_CLIENT = httpx.AsyncClient  # captured before any monkeypatching


def _gemini_client(handler):
    def factory(*args, **kwargs):
        return _REAL_CLIENT(transport=httpx.MockTransport(handler))

    return factory


async def test_gemini_test_passes(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _gemini_client(_gemini_ok))
    result = await GeminiDiscovery("k").test("gemini-x")
    assert (result.passed, result.reason) == (True, "ok")


async def test_gemini_test_maps_429(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _gemini_client(_gemini_429))
    result = await GeminiDiscovery("k").test("gemini-x")
    assert (result.passed, result.reason) == (False, "rate_limited")


async def test_gemini_test_maps_empty_candidates(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _gemini_client(_gemini_empty))
    result = await GeminiDiscovery("k").test("gemini-x")
    assert result.passed is False


_CATALOG = {
    "data": [
        {
            "id": "big-free",
            "context_length": 200_000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text->text"},
        },
        {
            "id": "paid",
            "context_length": 999_999,
            "pricing": {"prompt": "1", "completion": "2"},
            "architecture": {"modality": "text->text"},
        },
        {
            "id": "vision-only",
            "context_length": 50_000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "image->text"},
        },
        {
            "id": "small-free",
            "context_length": 8_000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text->text"},
        },
    ]
}


def _catalog_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_CATALOG)


async def test_openrouter_catalog_filters_and_ranks(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _gemini_client(_catalog_ok))
    models = await OpenRouterCatalog("k").list_free_models()
    ids = [m.model_id for m in models]
    assert ids == ["big-free", "small-free"]  # paid + non-text excluded, context desc
    assert all(m.pricing_prompt == 0 and m.pricing_completion == 0 for m in models)


async def test_openrouter_catalog_degrades_to_static_on_failure(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    adapter = OpenRouterCatalog("k")
    adapter.candidates = [FreeModel(model_id="static", provider="OpenRouter", context_length=1)]
    assert [m.model_id for m in await adapter.list_free_models()] == ["static"]


async def test_complete_raises_completion_error(monkeypatch) -> None:
    def gone(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", gone)
    with pytest.raises(CompletionError):
        await StubCompat(_chat_ok).complete("stub-1", "hi")

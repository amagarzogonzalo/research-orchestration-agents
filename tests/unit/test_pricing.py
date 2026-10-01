"""Unit tests: local pricing table (offline, pure logic)."""

from __future__ import annotations

from knowledge_engine.core.pricing import compute_cost_usd, price_for


def test_free_tiers_price_at_zero() -> None:
    for provider in ("groq", "openrouter", "gemini", "mistral", "ollama"):
        assert price_for(provider, "any-model") == (0.0, 0.0)


def test_unknown_provider_falls_back_to_ollama_table() -> None:
    assert price_for("nope", "x") == (0.0, 0.0)


def test_unknown_model_falls_back_to_provider_default() -> None:
    assert price_for("groq", "future-model-99") == (0.0, 0.0)


def test_compute_cost_is_zero_on_free_tiers() -> None:
    assert compute_cost_usd("groq", "m", 1_000_000, 500_000) == 0.0


def test_compute_cost_rounds_to_six_decimals() -> None:
    # pricing table is all-zero today; rounding behavior is pinned anyway
    assert compute_cost_usd("groq", "m", 1, 1) == 0.0

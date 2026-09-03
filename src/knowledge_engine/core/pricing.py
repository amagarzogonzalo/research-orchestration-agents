"""Local pricing table (ADR 0006: never trust vendor-reported cost).

Free tiers report $0 cost; we still track token counts and recompute an
internally-consistent `cost_usd` from this table for the observability layer.
Values are illustrative and intentionally $0/dollar-based for free tiers.
"""

from __future__ import annotations

from functools import lru_cache

# USD per 1M tokens: {provider: {model: (input, output)}}
# Free tiers => $0. Keep the table so a paid tier is trivially additive later
# (project rule: never route paid tiers; this table documents intent).
_PRICE_PER_1M: dict[str, dict[str, tuple[float, float]]] = {
    "groq": {"default": (0.0, 0.0)},
    "openrouter": {"default": (0.0, 0.0)},
    "gemini": {"default": (0.0, 0.0)},
    "mistral": {"default": (0.0, 0.0)},
    "ollama": {"default": (0.0, 0.0)},
}


@lru_cache(maxsize=64)
def price_for(provider: str, model: str) -> tuple[float, float]:
    """Return (input, output) USD-per-1M-tokens for a provider/model."""
    provider_table = _PRICE_PER_1M.get(provider, _PRICE_PER_1M["ollama"])
    return provider_table.get(model, provider_table["default"])


def compute_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Recompute cost from local pricing, not vendor claims."""
    price_in, price_out = price_for(provider, model)
    return round(
        (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out,
        6,
    )

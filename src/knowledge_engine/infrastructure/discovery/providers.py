"""Concrete free-model providers: Groq + Mistral (fixed OpenAI-compatible lists)."""

from __future__ import annotations

from knowledge_engine.core.discovery import FreeModel
from knowledge_engine.infrastructure.discovery.openai_compat import OpenAICompatDiscovery


class GroqDiscovery(OpenAICompatDiscovery):
    """Groq free models (api.groq.com)."""

    endpoint = "https://api.groq.com/openai/v1"
    candidates: list[FreeModel] = [
        FreeModel(model_id="openai/gpt-oss-120b", provider="Groq", context_length=128_000),
        FreeModel(model_id="openai/gpt-oss-20b", provider="Groq", context_length=128_000),
        FreeModel(model_id="groq/compound-mini", provider="Groq", context_length=128_000),
    ]


class MistralDiscovery(OpenAICompatDiscovery):
    """Mistral free models (api.mistral.ai)."""

    endpoint = "https://api.mistral.ai/v1"
    candidates: list[FreeModel] = [
        FreeModel(model_id="mistral-small-latest", provider="Mistral", context_length=32_000),
        FreeModel(model_id="open-mistral-7b", provider="Mistral", context_length=8_000),
    ]


__all__ = ["GroqDiscovery", "MistralDiscovery"]

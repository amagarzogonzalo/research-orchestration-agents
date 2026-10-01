"""Free-LLM discovery service (adapted from the digest_agent pattern)."""

from __future__ import annotations

from knowledge_engine.infrastructure.discovery.fallback_discovery import FallbackDiscovery
from knowledge_engine.infrastructure.discovery.gemini_discovery import GeminiDiscovery
from knowledge_engine.infrastructure.discovery.model_selection import (
    ModelSelectionPolicy,
    SelectionDecision,
    SelectionState,
)
from knowledge_engine.infrastructure.discovery.openai_compat import OpenAICompatDiscovery
from knowledge_engine.infrastructure.discovery.openrouter_catalog import OpenRouterCatalog
from knowledge_engine.infrastructure.discovery.providers import GroqDiscovery, MistralDiscovery
from knowledge_engine.infrastructure.discovery.service import ModelDiscoverer, ModelSelectionError
from knowledge_engine.infrastructure.discovery.text_utils import strip_reasoning_prefix

__all__ = [
    "FallbackDiscovery",
    "GeminiDiscovery",
    "GroqDiscovery",
    "MistralDiscovery",
    "ModelDiscoverer",
    "ModelSelectionError",
    "ModelSelectionPolicy",
    "OpenAICompatDiscovery",
    "OpenRouterCatalog",
    "SelectionDecision",
    "SelectionState",
    "strip_reasoning_prefix",
]

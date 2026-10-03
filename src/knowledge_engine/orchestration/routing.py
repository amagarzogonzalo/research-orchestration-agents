"""Deterministic intent routing for the unified entry (no LLM on the fast path).

Explicit ``mode`` overrides always win (PaperPilot explicit-mode coverage).
Otherwise cheap heuristics decide chat/memory/quick/deep; genuinely ambiguous
input returns ``undecided`` so the intent node can spend one MICRO call.
"""

from __future__ import annotations

import re

from knowledge_engine.schemas.decomposition import IntentKind

_VALID = {i.value for i in IntentKind}

_GREETINGS = {
    "hi",
    "hello",
    "hey",
    "yo",
    "thanks",
    "thank you",
    "bye",
    "goodbye",
    "ok",
    "okay",
    "yes",
    "no",
}

_MEMORY_MARKERS = ("remember", "my preference", "what do you know about me", "forget that", "my name is")

_RESEARCH_MARKERS = (
    "compare",
    "survey",
    "landscape",
    "evolution",
    "trade-off",
    "tradeoff",
    "evaluation",
    "systematic",
    "deep dive",
    "state of the art",
    "literature",
)


def route_intent(query: str, mode_override: str | None) -> str:
    """Route a query to an intent without spending an LLM call.

    Args:
        query: Raw user input.
        mode_override: Explicit mode (``chat``/``memory``/``quick``/``deep``).

    Returns:
        str: Intent value (possibly ``undecided`` for the LLM fallback).
    """
    if mode_override:
        override = mode_override.strip().lower()
        if override in _VALID and override != IntentKind.UNDECIDED.value:
            return override
    text = query.strip()
    lowered = text.lower()
    if not lowered or lowered in _GREETINGS:
        return IntentKind.CHAT.value
    if any(marker in lowered for marker in _MEMORY_MARKERS):
        return IntentKind.MEMORY.value
    if any(marker in lowered for marker in _RESEARCH_MARKERS) or len(text) > 120:
        return IntentKind.DEEP.value
    if re.match(r"^(what|who|when|where|which|how|is|are|can|does)\b", lowered) or lowered.endswith("?"):
        return IntentKind.QUICK.value
    return IntentKind.UNDECIDED.value


__all__ = ["route_intent"]

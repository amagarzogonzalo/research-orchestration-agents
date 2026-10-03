"""intent node: unified-entry routing (deterministic fast path + LLM fallback).

The cheap :func:`route_intent` heuristic decides without spending a call;
only genuinely ambiguous input (plus an explicit-mode miss) costs one MICRO
call, and any provider failure fails open to ``deep`` (research is the default).
"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.routing import route_intent
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.decomposition import IntentKind
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

_INTENT_SYSTEM = "You are an intent classifier. Reply with exactly one word: chat, memory, quick, or deep."
_VALID = {i.value for i in IntentKind} - {IntentKind.UNDECIDED.value}

INTENT_NODE = "intent"


class IntentNode(WorkflowNode):
    """Route the query to an intent, spending an LLM call only when unsure."""

    __slots__ = ("provider",)

    def __init__(self, provider: ProviderPort | None = None) -> None:
        """Create an IntentNode.

        Args:
            provider: Optional provider for the ambiguous-input fallback.
        """
        self.provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping with the ``intent`` write.
        """
        query = state.get("query", "")
        intent = route_intent(query, state.get("mode"))
        if intent != IntentKind.UNDECIDED.value or self.provider is None:
            return {"intent": intent if intent != IntentKind.UNDECIDED.value else IntentKind.DEEP.value}
        try:
            res = await self.provider.complete(
                ChatRequest.of(
                    _INTENT_SYSTEM,
                    f"Classify intent for this research query: {query}",
                    affinity=Affinity.MICRO,
                    max_tokens=10,
                )
            )
            word = res.message.content.strip().lower().split()[0]
            return {"intent": word if word in _VALID else IntentKind.DEEP.value}
        except Exception as exc:  # noqa: BLE001 - fail open to research on provider outage
            logger.warning("intent fallback failed (%s); defaulting to deep", exc)
            return {"intent": IntentKind.DEEP.value}


__all__ = ["INTENT_NODE", "IntentNode"]

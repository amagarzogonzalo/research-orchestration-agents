"""relevance node: cheap LLM boolean filter over resolved sources (LLM step).

One batched LLM call instead of one-per-source: sends all source titles +
abstracts in a single MICRO-affinity request and asks for a JSON list of
relevant ids. This is faster, cheaper, and gentler on rate limits than N calls.
A failed batch (network/parse) degrades to *keeping everything* rather than
silently dropping the corpus — the judge stage still has something to score.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

_RELEVANCE_SYSTEM = (
    "You are a research librarian. Given a research topic and a list of papers, "
    "decide which papers are directly relevant to the topic. "
    'Reply ONLY with JSON: {"relevant_ids": ["id1", "id2", ...]}. No prose.'
)


class RelevanceNode(WorkflowNode):
    """Filter sources in one batched LLM call (JSON relevant-id list)."""

    __slots__ = ("provider", "_system", "max_tokens")

    def __init__(self, provider: ProviderPort, *, max_tokens: int = 400) -> None:
        """Create a RelevanceNode.

        Args:
            provider: Provider key for rate limiting.
            max_tokens: Maximum tokens.
        """
        self.provider = provider
        self._system = _RELEVANCE_SYSTEM
        self.max_tokens = max_tokens

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        sources = state.get("sources", [])
        if not sources:
            return {"filtered": []}
        ledger = "\n".join(f"[{i}] {s.id} | {s.title}" for i, s in enumerate(sources))
        query = state.get("query", "")
        user = f"Research topic: {query}\n\nPapers:\n{ledger}\n\nRelevant papers:"
        ids: list[str] | None = None
        try:
            res = await self.provider.complete(
                ChatRequest.of(self._system, user, affinity=Affinity.MICRO, max_tokens=self.max_tokens)
            )
            ids = _parse_relevant_ids(res.message.content)
        except Exception as exc:  # noqa: BLE001 - degrade to keep-all on failure
            logger.warning("relevance batch failed (%s); keeping all %d sources", exc, len(sources))

        if ids is None:
            return {"filtered": list(sources)}  # degrade: keep everything for the judge
        id_set = set(ids)
        return {"filtered": [s for s in sources if s.id in id_set]}


def _parse_relevant_ids(content: str) -> list[str] | None:
    """Extract relevant source ids from the model's JSON reply.

        Returns None when the reply cannot be parsed (caller keeps everything).

    Args:
        content: Content text.

    Returns:
        list[str] | None: Strings or None.

    """
    if not content:
        return None
    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        payload = json.loads(match.group(0)) if match else json.loads(content)
        raw = payload.get("relevant_ids") or payload.get("ids") or []
        return [str(x) for x in raw]
    except (json.JSONDecodeError, AttributeError, TypeError):
        logger.warning("unparseable relevance reply: %r", content[:120])
        return None


__all__ = ["RelevanceNode"]

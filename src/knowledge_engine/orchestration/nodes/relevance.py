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

# Maximum sources per relevance call: reasoning-model substitutes burn the
# token budget deliberating over long ledgers and return truncated-empty
# verdicts (finish_reason=length), silently degrading to keep-all. Batching
# keeps every call small enough to answer; a failed batch keeps just its own
# slice, never the whole corpus.
_RELEVANCE_BATCH = 25

_RELEVANCE_SYSTEM = (
    "You are a research librarian. Given a research topic and a list of papers, "
    "decide which papers are directly relevant to the topic. Require topical "
    "overlap: drop items whose title shares only generic words (requirements, "
    "development, local, model, analysis, study) with the query, especially "
    "when their venue is off-domain (law, medicine, social science for a "
    "computing/engineering topic). For practical how-to, tooling-comparison, or "
    "framework questions, treat practitioner sources (documentation, "
    "engineering blogs, encyclopedia overviews) as first-class evidence — do "
    "not demote them for lacking peer review. For scholarly questions, prefer "
    "peer-reviewed journals and highly-cited primary sources over aggregators "
    "when both cover the same claim — but keep unique-coverage items. "
    'Reply ONLY with JSON: {"relevant_ids": ["id1", "id2", ...]}. No prose.'
)


def _ledger_line(src: object) -> str:
    """Render one ledger line with authority signals.

    Lines carry source IDs only (no numeric indices): models shown ``[8] id``
    reply with bare indices, which are unresolvable (0- vs 1-based unknown)
    and silently filter everything out.

    Args:
        src: Source record.

    Returns:
        str: Ledger line.
    """
    if isinstance(src, dict):
        sid = str(src.get("id", ""))
        title = str(src.get("title", ""))
        venue = str(src.get("venue") or "")
    else:
        sid = str(getattr(src, "id", ""))
        title = str(getattr(src, "title", ""))
        venue = str(getattr(src, "venue", "") or "")
    # Titles + venue only: years and cite counts bloat the ledger without
    # helping topicality verdicts, and reasoning-model substitutes burn the
    # token budget deliberating over long ledgers (empty verdicts).
    line = f"{sid} | {title}"
    if venue:
        line += f" | {venue}"
    return line


class RelevanceNode(WorkflowNode):
    """Filter sources in batched LLM calls (JSON relevant-id lists)."""

    __slots__ = ("provider", "_system", "max_tokens")

    def __init__(self, provider: ProviderPort, *, max_tokens: int = 1500) -> None:
        """Create a RelevanceNode.

        Args:
            provider: Provider key for rate limiting.
            max_tokens: Maximum tokens. Sized for reasoning-model
                substitutes: OpenRouter may route the cheap slot to a
                reasoning model whose deliberation must fit alongside the
                JSON verdict — 400 truncated it to empty (finish_reason
                length), silently degrading to keep-all.
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
        query = state.get("query", "")
        kept: list[Any] = []
        for start in range(0, len(sources), _RELEVANCE_BATCH):
            batch = list(sources[start : start + _RELEVANCE_BATCH])
            ids = await self._verdict_ids(query, batch)
            if ids is None:
                kept.extend(batch)  # degrade: keep this slice for the judge
            else:
                id_set = set(ids)
                kept.extend(s for s in batch if _src_id(s) in id_set)
        return {"filtered": kept}

    async def _verdict_ids(self, query: str, batch: list[Any]) -> list[str] | None:
        """Ask the model which batch members are relevant (single attempt).

        A retry was tried and removed: recorded failures are structural
        (reasoning models deliberating past the token budget), never
        transient — retrying doubled the waste without converting once.

        Args:
            query: Research topic.
            batch: Source records slice.

        Returns:
            list[str] | None: Relevant ids, or None on failure (keep batch).
        """
        ledger = "\n".join(_ledger_line(s) for s in batch)
        user = f"Research topic: {query}\nPapers:\n{ledger}\n\nRelevant papers (JSON ids only):"
        try:
            res = await self.provider.complete(
                ChatRequest.of(self._system, user, affinity=Affinity.MICRO, max_tokens=self.max_tokens)
            )
            return _parse_relevant_ids(res.message.content)
        except Exception as exc:  # noqa: BLE001 - degrade to keep-batch on failure
            logger.warning("relevance batch failed (%s); keeping %d sources", exc, len(batch))
            return None


def _src_id(src: object) -> str:
    """Source id from a record or its dict snapshot.

    Args:
        src: SourceRecord or raw mapping.

    Returns:
        str: Record id (empty when absent).
    """
    if isinstance(src, dict):
        return str(src.get("id", "") or "")
    return str(getattr(src, "id", "") or "")


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

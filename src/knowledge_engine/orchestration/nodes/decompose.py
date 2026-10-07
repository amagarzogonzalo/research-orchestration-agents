"""decompose node: one bounded planning round (LLM step, fail-closed).

Asks the provider for a JSON plan of sub-queries tagged by retrieval source,
then enforces the shared budgets locally: depth guard stops recursion without
spending a call, output is truncated to ``MAX_SUBQUERIES``, and any parse
failure degrades to a single branch on the original query. Writes are deltas
(``llm_calls: +1``) so concurrent branches sum correctly via the add-reducer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.decomposition import MAX_DECOMPOSE_DEPTH, MAX_SUBQUERIES, SourceKind, SubQuery
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

_DECOMPOSE_SYSTEM = (
    "You are a research planner. Break the question into focused sub-queries, "
    "each tagged with its best retrieval source (search, scrape, mcp, browser). "
    'Reply ONLY with JSON: {"sub_queries": [{"query": str, "source": str, "rationale": str}]}.'
)

DECOMPOSE_NODE = "decompose"


class DecomposeNode(WorkflowNode):
    """Plan one fan-out round subject to depth and fan-out caps."""

    __slots__ = ("provider", "max_subqueries", "max_depth")

    def __init__(
        self, provider: ProviderPort, *, max_subqueries: int = MAX_SUBQUERIES, max_depth: int = MAX_DECOMPOSE_DEPTH
    ) -> None:
        """Create a DecomposeNode.

        Args:
            provider: Provider for the planning call.
            max_subqueries: Fan-out cap per round.
            max_depth: Deepest plannable depth.
        """
        self.provider = provider
        self.max_subqueries = max_subqueries
        self.max_depth = max_depth

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping with ``sub_queries`` + budget deltas.
        """
        query = state.get("query", "")
        depth = state.get("depth", 0)
        rounds = state.get("rounds", 0)
        if depth >= self.max_depth:
            logger.info("depth cap reached (%d); stopping decomposition", depth)
            return {"sub_queries": [], "rounds": rounds + 1, "llm_calls": 0}
        gaps = state.get("gaps", [])
        prompt = f"Return the plan as sub-queries JSON for: {query}"
        if gaps:
            prompt += f"\nEvidence gaps to cover: {'; '.join(gaps)}"
        if state.get("branch_notes"):
            prompt += f"\nKnown so far: {' | '.join(state['branch_notes'][-3:])}"[:2000]
        try:
            res = await self.provider.complete(
                ChatRequest.of(_DECOMPOSE_SYSTEM, prompt, affinity=Affinity.MICRO, max_tokens=600)
            )
            items = _parse_plan(res.message.content, query, depth + 1)
        except Exception as exc:  # noqa: BLE001 - fail closed to one branch on the query
            logger.warning("decompose failed (%s); falling back to single branch", exc)
            items = [_fallback(query, depth + 1)]
        return {
            "sub_queries": [item.model_dump(mode="json") for item in items[: self.max_subqueries]],
            "depth": depth + 1,
            "rounds": rounds + 1,
            "llm_calls": 1,
        }


def _parse_plan(raw: str, query: str, depth: int) -> list[SubQuery]:
    """Parse the provider's JSON plan, failing closed to one branch.

    Args:
        raw: Raw provider output.
        query: Original query (fallback branch).
        depth: Depth tag for planned branches.

    Returns:
        list[SubQuery]: Planned branches (at least one).
    """
    try:
        payload = json.loads(raw)
        entries = payload.get("sub_queries", [])
        items = [
            SubQuery(
                query=str(e.get("query", "")), source=_source_of(e), rationale=str(e.get("rationale", "")), depth=depth
            )
            for e in entries
            if e.get("query")
        ]
        return items or [_fallback(query, depth)]
    except (ValueError, AttributeError, TypeError):
        return [_fallback(query, depth)]


def _source_of(entry: dict[str, Any]) -> SourceKind:
    """Coerce a source tag, defaulting to search on unknown values.

    Args:
        entry: Raw sub-query mapping.

    Returns:
        SourceKind: Retrieval route.
    """
    try:
        return SourceKind(str(entry.get("source", "search")).lower())
    except ValueError:
        return SourceKind.SEARCH


def _fallback(query: str, depth: int) -> SubQuery:
    """Single-branch fallback preserving the original query.

    Args:
        query: Original query.
        depth: Depth tag.

    Returns:
        SubQuery: Fallback branch.
    """
    return SubQuery(query=query, source=SourceKind.SEARCH, rationale="planner fallback", depth=depth)


__all__ = ["DECOMPOSE_NODE", "DecomposeNode"]

"""clean node: drop duplicate sources before relevance (ADR wiki-loop0).

Pure node: one bounded unit of work against the injected
:class:`~knowledge_engine.core.ports.CleanPort`. Writes state only when
something was actually dropped, so a clean corpus replay is byte-identical
(close-never-delete: dropped ids survive in ``clean_stats.superseded_by``).
"""

from __future__ import annotations

from typing import Any

from knowledge_engine.core.ports import CleanPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState

CLEAN_NODE = "clean"


class CleanNode(WorkflowNode):
    """Dedup the raw federated source batch."""

    __slots__ = ("cleaner",)

    def __init__(self, cleaner: CleanPort) -> None:
        """Create a CleanNode.

        Args:
            cleaner: Clean port (tiered dedup adapter).
        """
        self.cleaner = cleaner

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: ``sources`` (kept) + ``clean_stats`` when anything
            was dropped; empty when the batch was already clean.
        """
        sources = state.get("sources", [])
        if not sources:
            return {}
        result = await self.cleaner.clean(list(sources))
        if not result.dropped:
            return {}
        return {
            "sources": result.kept,
            "clean_stats": result.model_dump(exclude={"kept"}),
        }


__all__ = ["CLEAN_NODE", "CleanNode"]

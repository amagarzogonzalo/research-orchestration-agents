"""ingest node: resolve sources for the query (Data step, ADR 0008 §2.2).

One bounded unit of work: run the federated search port and write the raw
:class:`SourceRecord` list back to state. Never formats prompts here — state
stores raw data only.
"""

from __future__ import annotations

from typing import Any

from knowledge_engine.core.ports import SearchPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState


class IngestNode(WorkflowNode):
    """Resolve scholarly sources for a query via an injected :class:`SearchPort`."""

    __slots__ = ("search", "limit")

    def __init__(self, search: SearchPort, *, limit: int = 10) -> None:
        """Create a IngestNode.

        Args:
            search: Scholarly search port.
            limit: Maximum records to return.
        """
        self.search = search
        self.limit = limit

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        sources = await self.search.search(state["query"], limit=self.limit)
        return {"sources": sources, "status": "running"}

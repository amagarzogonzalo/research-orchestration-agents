"""wiki_ingest node: file the rendered artifact into the wiki (ADR wiki-loop0).

Pure node: extracts the run's provenance and delegates all I/O to the
injected :class:`~knowledge_engine.core.ports.WikiIngestPort`. Fires after
render; verdicts (new/update/no_material) live in the adapter.
"""

from __future__ import annotations

from typing import Any

from knowledge_engine.core.ports import WikiIngestPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState

WIKI_INGEST_NODE = "wiki_ingest"


class WikiIngestNode(WorkflowNode):
    """Compile the finished run into the persistent wiki layer."""

    __slots__ = ("wiki",)

    def __init__(self, wiki: WikiIngestPort) -> None:
        """Create a WikiIngestNode.

        Args:
            wiki: Wiki ingest port.
        """
        self.wiki = wiki

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: ``wiki_ingest`` result mapping, or empty when the
            run produced no rendered artifact (ingest is skipped silently so
            failed runs never write wiki pages).
        """
        output_path = state.get("output_path")
        if not output_path:
            return {}
        result = await self.wiki.ingest(state.get("thread_id", ""), state.get("query", ""), output_path)
        return {"wiki_ingest": result.model_dump()}


__all__ = ["WIKI_INGEST_NODE", "WikiIngestNode"]

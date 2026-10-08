"""branch node: homogeneous leaf worker (composition, not duplication).

One branch = the existing leaf pipeline (ingest → relevance → fetch → synthesize)
run against a single :class:`SubQuery`, reusing the injected node instances.
Branches only write add-reducer keys (``branch_notes``/``bodies``/``llm_calls``
delta), so concurrent ``Send`` executions merge safely into the parent state.

Retrieval routing: ``mcp`` sub-queries use the injected MCP-backed search port
when present and fall back to federated search otherwise (no new MCP servers);
``browser``/``scrape`` sub-queries ride the scrape port's fetch path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from knowledge_engine.core.ports import SearchPort
from knowledge_engine.orchestration.nodes.bodies import FetchBodyNode
from knowledge_engine.orchestration.nodes.ingest import IngestNode
from knowledge_engine.orchestration.nodes.relevance import RelevanceNode
from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.decomposition import SourceKind, SubQuery
from knowledge_engine.schemas.source import BodyRef, DocumentBody

logger = logging.getLogger(__name__)

BRANCH_NODE = "branch"


class BranchNode(WorkflowNode):
    """Execute one planned sub-query through the shared leaf pipeline."""

    __slots__ = ("ingest", "relevance", "fetch_body", "synthesize", "mcp_search", "max_sources")

    def __init__(
        self,
        *,
        ingest: IngestNode,
        relevance: RelevanceNode,
        fetch_body: FetchBodyNode,
        synthesize: SynthesizeNode,
        mcp_search: SearchPort | None = None,
        max_sources: int = 3,
    ) -> None:
        """Create a BranchNode.

        Args:
            ingest: Federated-search ingest node (also used for scrape/browser routes).
            relevance: Batched relevance-filter node.
            fetch_body: Single-source fetch worker.
            synthesize: Leaf synthesis node.
            mcp_search: Optional MCP-backed search port for ``mcp`` sub-queries.
            max_sources: Bodies fetched per branch (evidence hierarchy cap).
        """
        self.ingest = ingest
        self.relevance = relevance
        self.fetch_body = fetch_body
        self.synthesize = synthesize
        self.mcp_search = mcp_search
        self.max_sources = max_sources

    async def run(self, state: WorkflowState | dict[str, Any]) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Parent state merged with the ``Send`` sub-query payload.

        Returns:
            dict[str, Any]: Reducer-safe writes only.
        """
        sub = _sub_query_of(state)
        query = sub.query
        sources = await self._retrieve(query, sub.source)
        sub_state: WorkflowState = {"query": query, "sources": sources}
        filtered = (await self.relevance.run(sub_state)).get("filtered", sources)
        bodies = await self._fetch_all(filtered[: self.max_sources])
        leaf: WorkflowState = {
            "query": query,
            "sources": sources,
            "filtered": filtered,
            "bodies": bodies,
            "iterations": 0,
        }
        notes = (await self.synthesize.run(leaf)).get("notes", [])
        # Contribute the actually-used sources to the add-reducer channel so
        # provenance, footnotes, and durable snapshots see branch evidence.
        return {"branch_notes": notes, "bodies": bodies, "llm_calls": 2, "branch_sources": filtered}

    async def _retrieve(self, query: str, source: SourceKind) -> list[Any]:
        """Retrieve sources via the routed port (MCP with search fallback).

        Args:
            query: Branch sub-query text.
            source: Retrieval route tag.

        Returns:
            list[Any]: Resolved source records.
        """
        if source == SourceKind.MCP and self.mcp_search is not None:
            return await self.mcp_search.search(query)
        if source == SourceKind.MCP:
            logger.info("mcp source requested without an MCP port; using federated search")
        update = await self.ingest.run({"query": query})
        resolved: list[Any] = update.get("sources", [])
        return resolved

    async def _fetch_all(self, sources: list[Any]) -> list[BodyRef | DocumentBody]:
        """Fetch bodies concurrently (bounded by the worker semaphore).

        Args:
            sources: Filtered sources to fetch.

        Returns:
            list[BodyRef | DocumentBody]: Fetched bodies as refs (failures skipped).
        """
        results = await asyncio.gather(*[self.fetch_body.run({"source": src}) for src in sources])
        bodies: list[BodyRef | DocumentBody] = []
        for update in results:
            bodies.extend(update.get("bodies", []))
        return bodies


def _sub_query_of(state: WorkflowState | dict[str, Any]) -> SubQuery:
    """Parse the ``Send`` payload, falling back to the parent query.

    Args:
        state: Parent state merged with the payload.

    Returns:
        SubQuery: Branch assignment.
    """
    raw = state.get("sub_query") if isinstance(state, dict) else None
    if isinstance(raw, dict):
        try:
            return SubQuery.model_validate(raw)
        except ValueError:
            pass
    query = state.get("query", "") if isinstance(state, dict) else ""
    return SubQuery(query=query or "(empty)", depth=int(state.get("depth", 1)) if isinstance(state, dict) else 1)


__all__ = ["BRANCH_NODE", "BranchNode"]

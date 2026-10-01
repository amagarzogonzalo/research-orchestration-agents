"""bodies nodes: Send map-reduce fan-out per filtered source (Data step).

* :class:`BodiesNode` — routing node: emits dynamic
  :class:`~langgraph.types.Send` tasks (one per filtered source) targeting
  ``fetch_body``. With zero filtered sources it routes straight to the join,
  so an empty relevance result never dead-ends.
* :class:`FetchBodyNode` — the worker: derives a fetchable URL for one source
  and writes a single :class:`DocumentBody` into ``bodies`` (add-reducer).

An individual source failing to fetch is tolerated (log + skip) so partial
fan-out never aborts the graph.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.types import Command, Send

from knowledge_engine.core.ports import ScrapePort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)

FETCH_NODE = "fetch_body"
JOIN_NODE = "synthesize"


class BodiesNode(WorkflowNode):
    """Fan-out router: returns :class:`Send` tasks per filtered source (map-reduce)."""

    __slots__ = ()

    async def run(self, state: WorkflowState | dict[str, Any]) -> dict[str, Any] | Command[Any]:
        # Fan out over the relevance-filtered set (research), or all sources
        # (summary — which skips the relevance gate).
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any] | Command[Any]: State update or routing command.

        """
        pool = state.get("filtered") or state.get("sources", [])
        sends = [Send(FETCH_NODE, {"source": src}) for src in pool]
        if sends:
            return self.command(goto=sends)
        logger.info("no sources to fetch; skipping body fan-out")
        return self.command(goto=JOIN_NODE)


class FetchBodyNode(WorkflowNode):
    """Worker: scrape one source to LLM-ready Markdown (bounded fan-out)."""

    __slots__ = ("scrape", "_sem")

    def __init__(self, scrape: ScrapePort, *, concurrency: int = 4) -> None:
        """Create a FetchBodyNode.

        Args:
            scrape: Scrape-to-markdown port.
            concurrency: Fetch concurrency.
        """
        self.scrape = scrape
        self._sem = asyncio.Semaphore(concurrency)

    async def run(self, state: WorkflowState | dict[str, Any]) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        src = _source_of(state)
        url = _derive_url(src)
        if not url:
            logger.info("no fetchable URL for %s; skipping body", src.id)
            return {"bodies": []}
        async with self._sem:
            try:
                body = await self.scrape.to_markdown(url)
                return {"bodies": [body]}
            except Exception as exc:  # noqa: BLE001 - partial fan-out tolerated
                logger.warning("scrape failed for %s: %s", src.id, exc)
                return {"bodies": []}


def _derive_url(src: SourceRecord) -> str | None:
    """Choose the most scrape-friendly URL for a source.

        HTML landing pages extract cleaner than raw PDFs (PDFs are binary and
        rejected by the content-quality guard), so per-source HTML pages outrank
        ``pdf_url``; the publisher PDF / DOI resolver is the fallback.

    Args:
        src: Source record.

    Returns:
        str | None: Text or None.

    """
    if src.id.startswith("arxiv:"):
        return f"https://arxiv.org/abs/{src.id.replace('arxiv:', '')}"
    if src.id.startswith("europe_pmc:"):
        _, _, rest = src.id.partition("europe_pmc:")
        source, _, ext_id = rest.partition(":")
        if source and ext_id:
            return f"https://europepmc.org/article/{source}/{ext_id}"
    if src.id.startswith("pubmed:"):
        pmid = src.id.replace("pubmed:", "")
        if pmid:
            return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    if src.pdf_url:
        return src.pdf_url
    if src.doi:
        return f"https://doi.org/{src.doi}"
    return None


def _source_of(state: WorkflowState | dict[str, Any]) -> SourceRecord:
    """The per-Send payload carries a single ``source`` (not the full state).

    Args:
        state: Current workflow state.

    Returns:
        SourceRecord: Resolved source record.

    Raises:
        ValueError: When the operation fails.

    """
    raw = state.get("source") if isinstance(state, dict) else None
    if raw is None:
        raise ValueError("fetch_body got a state without a 'source' payload")
    if isinstance(raw, SourceRecord):
        return raw
    return SourceRecord.model_validate(raw)


__all__ = ["BodiesNode", "FETCH_NODE", "JOIN_NODE", "FetchBodyNode"]

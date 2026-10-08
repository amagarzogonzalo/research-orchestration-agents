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
import re
from typing import Any

from langgraph.types import Command, Send

from knowledge_engine.core.ports import BodyStoragePort, ScrapePort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.source import SearchSource, SourceRecord

logger = logging.getLogger(__name__)

FETCH_NODE = "fetch_body"
JOIN_NODE = "synthesize"

# Cost bound on the scrape fan-out: every filtered source would otherwise be
# fetched (63-source federated unions blew both the call and token budgets).
# Scholarly records keep source-priority order up to the cap; general-web
# records (Wikipedia/DuckDuckGo/Tavily/Exa) get a reserved quota so the
# fallback tier is always represented in evals, regardless of scholarly volume.
_MAX_SCHOLARLY_BODIES = 16
_MAX_GENERAL_BODIES = 4
_GENERAL_SOURCES = frozenset(
    {SearchSource.WIKIPEDIA, SearchSource.DUCKDUCKGO, SearchSource.TAVILY, SearchSource.EXA, SearchSource.SERPER}
)


def _is_general_web(src: object) -> bool:
    """Whether a record belongs to the general-web fallback tier.

    Args:
        src: Source record (Pydantic or dict snapshot).

    Returns:
        bool: True for Wikipedia/DuckDuckGo/Tavily/Exa records.
    """
    source = src.get("source") if isinstance(src, dict) else getattr(src, "source", None)
    if not isinstance(source, str):
        return False
    try:
        return SearchSource(source) in _GENERAL_SOURCES
    except ValueError:
        return False


def select_scrape_pool(pool: list[Any]) -> list[Any]:
    """Bound the scrape fan-out, preserving pool order and the web tier.

    Args:
        pool: Relevance-filtered sources (or all sources when unfiltered).

    Returns:
        list[Any]: At most ``_MAX_SCHOLARLY_BODIES`` scholarly plus
            ``_MAX_GENERAL_BODIES`` general-web records, in pool order.
    """
    scholarly_taken = general_taken = 0
    chosen: list[Any] = []
    for src in pool:
        if _is_general_web(src):
            if general_taken < _MAX_GENERAL_BODIES:
                chosen.append(src)
                general_taken += 1
        elif scholarly_taken < _MAX_SCHOLARLY_BODIES:
            chosen.append(src)
            scholarly_taken += 1
    if len(chosen) < len(pool):
        logger.info("scrape pool capped: %d of %d sources fetched", len(chosen), len(pool))
    return chosen


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
        sends = [Send(FETCH_NODE, {"source": src}) for src in select_scrape_pool(list(pool))]
        if sends:
            return self.command(goto=sends)
        logger.info("no sources to fetch; skipping body fan-out")
        return self.command(goto=JOIN_NODE)


class FetchBodyNode(WorkflowNode):
    """Worker: scrape one source, persist via storage, write a BodyRef (ADR 0013)."""

    __slots__ = ("scrape", "storage", "_sem")

    def __init__(self, scrape: ScrapePort, *, storage: BodyStoragePort, concurrency: int = 6) -> None:
        """Create a FetchBodyNode.

        Args:
            scrape: Scrape-to-markdown port.
            storage: Body persistence; state carries refs, not markdown.
            concurrency: Fetch concurrency.
        """
        self.scrape = scrape
        self.storage = storage
        self._sem = asyncio.Semaphore(concurrency)

    async def run(self, state: WorkflowState | dict[str, Any]) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping (``bodies`` carries a single BodyRef).

        """
        src = _source_of(state)
        url = _derive_url(src)
        if not url:
            logger.info("no fetchable URL for %s; skipping body", src.id)
            return {"bodies": []}
        async with self._sem:
            try:
                body = await self.scrape.to_markdown(url)
                body.markdown = _strip_references(body.markdown)
                body.char_count = len(body.markdown)
                ref = await self.storage.save(body)
                return {"bodies": [ref]}
            except Exception as exc:  # noqa: BLE001 - partial fan-out tolerated
                logger.warning("scrape failed for %s: %s", src.id, exc)
                return {"bodies": []}


def _strip_references(markdown: str) -> str:
    """Cut a trailing references/bibliography section from scraped Markdown.

    Bibliography DOI lists are not evidence for claims, but models mine them
    for citation markers pointing at unretrieved papers. Only cuts when the
    heading sits past the first third (never gut the body on a false match).

    Args:
        markdown: Scraped Markdown.

    Returns:
        str: Markdown without the trailing bibliography.
    """
    matches = list(
        re.finditer(
            r"(?m)^(#{0,3}\s*)?(references|bibliography|works cited|literature cited)\s*$",
            markdown,
            re.IGNORECASE,
        )
    )
    if not matches:
        return markdown
    cut = matches[-1].start()
    if cut < len(markdown) // 3:
        return markdown
    return markdown[:cut].rstrip() + "\n"


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
    if src.id.startswith("huggingface_papers:"):
        # HF Papers IDs are arXiv IDs; the abs page scrapes cleanly.
        arxiv_id = src.id.replace("huggingface_papers:", "")
        if arxiv_id:
            return f"https://arxiv.org/abs/{arxiv_id}"
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

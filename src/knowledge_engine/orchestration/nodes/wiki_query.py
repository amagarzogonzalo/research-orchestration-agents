"""wiki_query node: internal-first lookup before live search (ADR wiki-loop0).

Pure node: one bounded unit of work against the injected
:class:`~knowledge_engine.core.ports.WikiQueryPort`. Two hit tiers:

* **Exact hit** (page's ``query_sha256`` equals sha256 of this query): writes
  ``wiki_hit=True`` + ``notes`` (best page text); the graph routes straight
  to render — zero live calls for a repeat question.
* **Scored hit** (term overlap only): records ``wiki_hits`` for observability
  and proceeds through the normal pipeline — routing on fuzzy matches is
  Loop 1+ (requires eval coverage first; keeps replay deterministic).

A miss writes nothing, so the miss path is bit-identical to the pre-wiki run.
"""

from __future__ import annotations

import hashlib
from typing import Any

from knowledge_engine.core.ports import WikiQueryPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState

WIKI_QUERY_NODE = "wiki_query"


def _query_sha(query: str) -> str:
    """SHA256 hex digest of the query (must match the adapter's ingest key).

    Args:
        query: Research query string.

    Returns:
        str: Hex digest.
    """
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class WikiQueryNode(WorkflowNode):
    """Consult the wiki before spending live search quota."""

    __slots__ = ("wiki", "limit")

    def __init__(self, wiki: WikiQueryPort, *, limit: int = 3) -> None:
        """Create a WikiQueryNode.

        Args:
            wiki: Wiki query port (local file store or Supabase adapter).
            limit: Maximum hits considered.
        """
        self.wiki = wiki
        self.limit = limit

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Empty on full miss; observability-only ``wiki_hits``
            on a scored-only hit; ``wiki_hit`` + ``notes`` + ``wiki_hits`` on
            an exact query hit.
        """
        query = state.get("query", "")
        hits = await self.wiki.search_wiki(query, limit=self.limit)
        if not hits:
            return {}
        exact = [h for h in hits if h.query_sha256 and h.query_sha256 == _query_sha(query)]
        if not exact:
            # Scored-only: record for observability, keep the normal pipeline.
            return {"wiki_hits": [h.model_dump() for h in hits]}
        best = await self.wiki.read_page(exact[0].slug)
        notes = [best] if best else []
        return {
            "wiki_hits": [h.model_dump() for h in hits],
            "wiki_hit": True,
            "notes": notes,
        }


__all__ = ["WIKI_QUERY_NODE", "WikiQueryNode"]

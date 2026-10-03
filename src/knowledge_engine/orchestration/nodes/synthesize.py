"""synthesize node: abstractive research synthesis (LLM step, ADR 0008).

The :class:`SynthesizeNode` formats a prompt from the *raw* state (bodies +
optional judge critique), calls the MACRO-affinity provider, and appends the
notes via the ``notes`` add-reducer so the evaluator-optimizer loop retains
each iteration. ``iterations`` is bumped here so the route function can bound
the loop (Saga compensation lives at the graph layer via error_handler).
"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_engine.core.exceptions import ProviderExhausted
from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.state import (
    MAX_SYNTHESIS_ITERATIONS,
    WorkflowNode,
    WorkflowState,
)
from knowledge_engine.schemas.provider import Affinity, ChatRequest
from knowledge_engine.schemas.source import DocumentBody

logger = logging.getLogger(__name__)

_SYNTHESIS_SYSTEM = (
    "You are a senior research synthesizer. Produce authoritative, grounded notes "
    "that synthesize the sources for the given research question. Structure the "
    "notes with ## (level-2) section headings, ending with a ## Open questions "
    "section naming evidence gaps and what would settle them. Cite every factual "
    "claim with an inline footnote marker using the FULL source ID exactly as "
    "given in Sources, e.g. "
    '"RAG grounds generation in retrieved passages [^openalex:W123]." At the end, '
    "add one definition line per cited source, e.g. "
    '"[^openalex:W123]: Paper Title Here. Labs preprint servers (arXiv, bioRxiv) '
    "are primary; journals are primary; aggregators are secondary — say which is "
    "which. Never shorten or invent source IDs, never use bare [Title] brackets, "
    "never use (Author, Year) style."
)


class SynthesizeNode(WorkflowNode):
    """Deterministic port-based synthesis (the offline-testable default)."""

    __slots__ = ("provider", "max_iterations")

    def __init__(self, provider: ProviderPort, *, max_iterations: int = MAX_SYNTHESIS_ITERATIONS) -> None:
        """Create a SynthesizeNode.

        Args:
            provider: Provider key for rate limiting.
            max_iterations: Maximum iterations.
        """
        self.provider = provider
        self.max_iterations = max_iterations

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        critique = state.get("judge_critique")
        prompt = self._build_prompt(state, critique)
        req = ChatRequest.of(
            system=_SYNTHESIS_SYSTEM,
            user=prompt,
            affinity=Affinity.MACRO,
            max_tokens=1500,
        )
        res = await self.provider.complete(req)
        return {"notes": [res.message.content], "iterations": state.get("iterations", 0) + 1}

    @staticmethod
    def _build_prompt(state: WorkflowState, critique: str | None) -> str:
        # Ground the synthesis on scraped bodies when available; otherwise fall
        # back to source titles + abstracts so the LLM is never ungrounded.
        """Build prompt.

        Args:
            state: Current workflow state.
            critique: Critique text.

        Returns:
            str: Result text.

        """
        if state.get("bodies"):
            evidence = "\n---\n".join(_body_text(b) for b in state["bodies"])
        else:
            parts = []
            for src in state.get("filtered") or state.get("sources", []):
                # IDs are part of the evidence: the model can only cite exact
                # source IDs it has actually seen (see _SYNTHESIS_SYSTEM).
                parts.append(f"[{_src_id(src)}]\n{_src_title(src)}\n{_src_abstract(src)[:2000]}")
            evidence = "\n---\n".join(parts) or "(no sources)"
        prompt = (
            f"Synthesize the research findings into structured notes for query:\n"
            f"{state.get('query', '')}\n\nSources:\n{evidence}"
        )
        if critique:
            prompt += f"\n\nReviewer feedback to address:\n{critique}"
        return prompt


def _body_text(body: object) -> str:
    """Coerce a DocumentBody (Pydantic) or its raw dict snapshot to a snippet.

    Args:
        body: Document body.

    Returns:
        str: Result text.

    """
    if isinstance(body, DocumentBody):
        title, markdown = body.title or body.url, body.markdown
    elif isinstance(body, dict):
        title = body.get("title") or body.get("url") or ""
        markdown = body.get("markdown") or ""
    else:
        title, markdown = "", ""
    return f"{title}\n{markdown[:4000]}"


def _src_id(src: object) -> str:
    """ID from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Source ID.
    """
    if isinstance(src, dict):
        return src.get("id", "") or ""
    return getattr(src, "id", "") or ""


def _src_title(src: object) -> str:
    """Title from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Result text.

    """
    if isinstance(src, dict):
        return src.get("title", "") or ""
    return getattr(src, "title", "") or ""


def _src_abstract(src: object) -> str:
    """Abstract from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Result text.

    """
    if isinstance(src, dict):
        return src.get("abstract", "") or ""
    return getattr(src, "abstract", "") or ""


__all__ = ["SynthesizeNode", "ProviderExhausted"]

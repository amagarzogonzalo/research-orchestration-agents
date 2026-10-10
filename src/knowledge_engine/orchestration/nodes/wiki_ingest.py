"""wiki_ingest node: compile the finished run into the wiki (ADR wiki-loop0).

Pure node. Layer 1 (always): guaranteed deterministic filing of the rendered
article (verdicts live in the adapter). Layer 2 (opt-in via
``settings.wiki_extract`` + injected provider): the Karpathy two-step
compile — analysis -> generation — files concept/entity DRAFTS for human
review, addressing prior rejection feedback. Optional thesis mode
(``settings.thesis``) files a for/against verdict page.

Degrade-safe enrichment: the deterministic source page is ALWAYS filed first;
an LLM extraction/thesis failure (transport or validation) records a
``wiki_extract_error`` note and continues — the research deliverable is never
sunk by optional enrichment, and the placeholder schema gate ensures nothing
poisonous is ever written before validation passes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.core.ports import ProviderPort, WikiIngestPort
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState, read_setting
from knowledge_engine.schemas.provider import Affinity, ChatRequest
from knowledge_engine.schemas.wiki import WikiExtraction, WikiThesis

logger = logging.getLogger(__name__)

WIKI_INGEST_NODE = "wiki_ingest"

_EXTRACT_SYSTEM = (
    "You are a wiki librarian. Distill the source into pages. "
    'Reply ONLY with JSON: {"concepts": [str], "entities": [str], "summary": str, '
    '"contradictions": [str]}. Concepts/entities must be concrete names found in '
    "the source — never placeholders like n/a, unknown, todo."
)
_THESIS_SYSTEM = (
    "You are a thesis investigator. Weigh the evidence in the notes. "
    'Reply ONLY with JSON: {"claim": str, "for_claims": [str], "against_claims": [str], '
    '"verdict": str, "sources": [str]}.'
)


class WikiIngestNode(WorkflowNode):
    """Compile the finished run into the persistent wiki layer."""

    __slots__ = ("wiki", "provider", "max_wiki_pages")

    def __init__(
        self,
        wiki: WikiIngestPort,
        provider: ProviderPort | None = None,
        *,
        max_wiki_pages: int = 8,
    ) -> None:
        """Create a WikiIngestNode.

        Args:
            wiki: Wiki ingest port.
            provider: Optional provider for the extraction/thesis steps
                (used only when the run's settings opt in).
            max_wiki_pages: Default cap on concept/entity pages per ingest.
        """
        self.wiki = wiki
        self.provider = provider
        self.max_wiki_pages = max_wiki_pages

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: ``wiki_ingest`` verdict mapping (+``wiki_pages``
            and ``llm_calls`` deltas when extraction/thesis ran, or a
            ``wiki_extract_error`` note when enrichment degraded), or empty
            when the run produced no rendered artifact. Enrichment failure
            never fails the run — see :meth:`_extract`.
        """
        output_path = state.get("output_path")
        if not output_path:
            return {}
        result = await self.wiki.ingest(state.get("thread_id", ""), state.get("query", ""), output_path)
        out: dict[str, Any] = {"wiki_ingest": result.model_dump()}

        settings = state.get("settings") or {}
        if settings.get("wiki_save_answers"):
            answer_page = await self.wiki.file_answer(state.get("thread_id", ""), state.get("query", ""), output_path)
            out["wiki_answer"] = answer_page
            out["wiki_pages"] = [answer_page]

        provider = self.provider
        if not (settings.get("wiki_extract") and provider is not None):
            return out

        feedback = await self.wiki.feedback_for(state.get("query", ""))
        cap = read_setting(state, "max_wiki_pages", self.max_wiki_pages)
        try:
            extraction = await self._extract(state, feedback)
            pages = await self.wiki.compile_extraction(
                state.get("thread_id", ""), state.get("query", ""), extraction, feedback
            )
        except Exception as exc:  # noqa: BLE001 - extraction degrades: source page is already guaranteed
            # Transport/validation failure on the OPTIONAL enrichment must not
            # sink the research deliverable. Nothing poisonous is written
            # (the schema gate rejects placeholders before any draft exists);
            # the failure is reported on the run for observability.
            logger.warning("wiki extraction degraded (%s); deterministic source page preserved", exc)
            out["wiki_extract_error"] = str(exc)
            return out
        out["wiki_pages"] = (out.get("wiki_pages") or []) + pages[:cap]
        out["llm_calls"] = 1
        if len(pages) > cap:
            logger.warning("wiki extraction capped: %d drafts -> %d", len(pages), cap)

        claim = settings.get("thesis")
        if claim:
            try:
                thesis = await self._thesis(state, str(claim), feedback)
                page = await self.wiki.file_thesis(state.get("thread_id", ""), thesis)
                out["wiki_pages"] = (out.get("wiki_pages") or []) + [page]
                out["llm_calls"] = int(out.get("llm_calls", 0)) + 1
            except Exception as exc:  # noqa: BLE001 - optional step degrades, never fails the run
                logger.warning("thesis filing degraded (%s); continuing without verdict page", exc)
        return out

    async def _extract(self, state: WorkflowState, feedback: list[str]) -> WikiExtraction:
        """One LLM analysis pass over the run's context (fail-closed).

        Args:
            state: Current workflow state.
            feedback: Rejection feedback lines to address.

        Returns:
            WikiExtraction: Validated payload.

        Raises:
            KnowledgeEngineError: When the provider call or validation fails
                (placeholders are rejected by the schema gate).
        """
        assert self.provider is not None
        prompt = f"Distill: {state.get('query', '')}"
        if feedback:
            prompt += "\nPrior rejections to address: " + " | ".join(feedback[:3])
        try:
            res = await self.provider.complete(
                ChatRequest.of(_EXTRACT_SYSTEM, prompt, affinity=Affinity.MICRO, max_tokens=600)
            )
            return WikiExtraction.model_validate(json.loads(res.message.content))
        except Exception as exc:
            raise KnowledgeEngineError(f"wiki extraction failed (source page preserved): {exc}") from exc

    async def _thesis(self, state: WorkflowState, claim: str, feedback: list[str]) -> WikiThesis:
        """One LLM verdict pass over the synthesized notes.

        Args:
            state: Current workflow state.
            claim: The investigated claim.
            feedback: Rejection feedback lines to address.

        Returns:
            WikiThesis: Validated payload.
        """
        assert self.provider is not None
        notes_text = " | ".join(state.get("notes", []))[:2000]
        prompt = f"Claim: {claim}\nNotes: {notes_text}"
        if feedback:
            prompt += "\nPrior rejections to address: " + " | ".join(feedback[:3])
        res = await self.provider.complete(
            ChatRequest.of(_THESIS_SYSTEM, prompt, affinity=Affinity.MICRO, max_tokens=600)
        )
        return WikiThesis.model_validate(json.loads(res.message.content))


__all__ = ["WIKI_INGEST_NODE", "WikiIngestNode"]

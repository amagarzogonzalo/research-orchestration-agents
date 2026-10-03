"""critic node: structured rubric feedback on merged evidence (LLM step).

The critic advises, the judge decides (SOLID separation): the critic scores
coverage/diversity/grounding, names contradictions + unanswered questions, and
extends ``gaps`` for the replan route. Any provider failure or unparsable reply
falls back to the deterministic rubric verdict — feedback is never lost, and a
dead critic can never stall the graph.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.guardrails import rubric_assess
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.decomposition import CriticReport, CriticVerdict
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

CRITIC_NODE = "critic"

_CRITIC_SYSTEM = (
    "You are a research critic. Assess coverage, diversity, grounding, "
    "contradictions, and unanswered questions in the merged findings. "
    'Reply ONLY with JSON: {"verdict": "sufficient"|"replan", "coverage": float, '
    '"diversity": float, "grounding": float, "contradictions": [str], '
    '"unanswered": [str], "revision_guidance": str}.'
)


class CriticNode(WorkflowNode):
    """Produce structured feedback for the replan route and the judge."""

    __slots__ = ("provider",)

    def __init__(self, provider: ProviderPort) -> None:
        """Create a CriticNode.

        Args:
            provider: Provider for the critique call.
        """
        self.provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping with extended gaps + critic report.
        """
        notes = list(state.get("notes", []) or state.get("branch_notes", []))
        query = state.get("query", "")
        try:
            res = await self.provider.complete(
                ChatRequest.of(
                    _CRITIC_SYSTEM,
                    f"Return the critic rubric assessment for: {query}\n\nFindings:\n{chr(10).join(notes)}"[:6000],
                    affinity=Affinity.MACRO,
                    max_tokens=600,
                )
            )
            report = _parse_report(res.message.content, state)
        except Exception as exc:  # noqa: BLE001 - dead critic falls back, never stalls
            logger.warning("critic failed (%s); using deterministic fallback", exc)
            report = _fallback_report(state)
        gaps = list(dict.fromkeys([*state.get("gaps", []), *report.unanswered, *report.contradictions]))
        return {"gaps": gaps, "critic_report": report.model_dump(mode="json"), "llm_calls": 1}


def _parse_report(raw: str, state: WorkflowState) -> CriticReport:
    """Parse the provider reply, falling back deterministically.

    Args:
        raw: Raw provider output.
        state: Current workflow state.

    Returns:
        CriticReport: Parsed or fallback report.
    """
    try:
        payload = json.loads(raw)
        return CriticReport.model_validate(payload)
    except (ValueError, TypeError):
        return _fallback_report(state)


def _fallback_report(state: WorkflowState) -> CriticReport:
    """Deterministic verdict from the rubric heuristics.

    Args:
        state: Current workflow state.

    Returns:
        CriticReport: Fallback report.
    """
    score, gaps = rubric_assess(state.get("sources", []), state.get("bodies", []), state.get("branch_notes", []))
    verdict = CriticVerdict.SUFFICIENT if score >= 0.7 else CriticVerdict.REPLAN
    return CriticReport(
        verdict=verdict,
        coverage=round(min(1.0, len(state.get("bodies", [])) / 3.0), 3),
        revision_guidance="; ".join(gaps) or "evidence sufficient",
        unanswered=list(gaps),
    )


__all__ = ["CRITIC_NODE", "CriticNode"]

"""aggregate node: merge branch notes + score the evidence rubric (LLM step).

Joins the concurrent fan-out: one MACRO call fuses ``branch_notes`` into a
single note, then the deterministic rubric assesses sufficiency and records
``rubric_score``/``gaps`` for the replan route. Empty fan-out is total (writes
empty notes) so the route can fail safe to the judge.
"""

from __future__ import annotations

import logging
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.guardrails import rubric_assess
from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

_AGGREGATE_SYSTEM = "You are a senior research editor. Merge the branch findings into one coherent, sourced summary."

AGGREGATE_NODE = "aggregate"


class AggregateNode(WorkflowNode):
    """Fuse branch notes and attach the rubric verdict."""

    __slots__ = ("provider",)

    def __init__(self, provider: ProviderPort) -> None:
        """Create an AggregateNode.

        Args:
            provider: Provider for the merge call.
        """
        self.provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping with merged notes + rubric verdict.
        """
        branch_notes = list(state.get("branch_notes", []))
        if not branch_notes:
            logger.info("empty fan-out; skipping merge")
            return {"notes": [], "rubric_score": 0.0, "gaps": ["coverage: no branches produced notes"]}
        fused = "\n---\n".join(branch_notes)
        res = await self.provider.complete(
            ChatRequest.of(
                _AGGREGATE_SYSTEM,
                f"Merge these branch findings for: {state.get('query', '')}\n\n{fused}"[:6000],
                affinity=Affinity.MACRO,
                # 2500: deep merges of 7+ branch notes truncated at 1500.
                max_tokens=2500,
            )
        )
        score, gaps = rubric_assess(state.get("sources", []), state.get("bodies", []), branch_notes)
        return {"notes": [res.message.content], "rubric_score": score, "gaps": gaps, "llm_calls": 1}


__all__ = ["AGGREGATE_NODE", "AggregateNode"]

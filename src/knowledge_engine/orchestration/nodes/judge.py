"""judge node: LLM-as-a-judge for the evaluator-optimizer loop (LLM step).

Scores groundedness + citation integrity, writes ``judge_score`` /
``judge_critique``, and exposes a pure :func:`judge_route` that decides
whether to re-synthesize or hand off to the review gate. The bounded loop
stop is the job of :func:`judge_route` (Saga discipline: never spin).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.orchestration.state import (
    MAX_SYNTHESIS_ITERATIONS,
    WorkflowNode,
    WorkflowState,
)
from knowledge_engine.schemas.provider import Affinity, ChatRequest

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "Score groundedness and citation integrity 0-1 on the synthesis. "
    'Reply as JSON {"score": float, "pass": bool, "critique": str}.'
)

REVIEW_NODE = "review"
SYNTHESIZE_NODE = "synthesize"
JUDGE_NODE = "judge"


class JudgeNode(WorkflowNode):
    """Evaluate synthesis quality via an injected :class:`ProviderPort`."""

    __slots__ = ("provider",)

    def __init__(self, provider: ProviderPort) -> None:
        """Create a JudgeNode.

        Args:
            provider: Provider key for rate limiting.
        """
        self.provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        notes = state.get("notes", [])
        if not notes:
            return {"judge_score": None, "judge_critique": ""}
        req = ChatRequest.of(
            system=_JUDGE_SYSTEM,
            user=notes[-1],
            affinity=Affinity.MACRO,
            max_tokens=300,
        )
        raw = ""
        try:
            res = await self.provider.complete(req)
            raw = res.message.content
            passed = '"pass": true' in raw or '"pass": True' in raw
            return {
                "judge_score": _parse_score(raw),
                "judge_critique": "" if passed else raw,
            }
        except Exception as exc:  # noqa: BLE001 - judge unavailable -> accept on merit
            logger.error("judge failed: %s", exc)
            return {"judge_score": 0.0, "judge_critique": "judge unavailable; accept on merit"}


def judge_route(state: WorkflowState) -> str:
    """Route: iterate on synthesis while critique non-empty + budget left.

    Args:
        state: Current workflow state.

    Returns:
        str: Result text.

    """
    if state.get("judge_critique") and state.get("iterations", 0) < MAX_SYNTHESIS_ITERATIONS:
        return SYNTHESIZE_NODE
    return REVIEW_NODE


def _parse_score(raw: str) -> float:
    """Parse score.

    Args:
        raw: Raw payload.

    Returns:
        float: Computed value.
    """
    m = re.search(r"\d+\.?\d*", raw)
    if m:
        return min(1.0, max(0.0, float(m.group())))
    try:
        return float(json.loads(raw).get("score", 0.0))
    except Exception:
        return 0.0


__all__ = ["JUDGE_NODE", "JudgeNode", "REVIEW_NODE", "SYNTHESIZE_NODE", "judge_route"]

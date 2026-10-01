"""review node: durable human-in-the-loop gate (User-input step, ADR 0008).

Three modes, selected via injected callbacks so it stays unit-testable:

* ``auto-approve`` — headless: write ``review_dir="approved"`` and route to
  render (default; offline tests never pause).
* ``interrupt`` — run :func:`langgraph.types.interrupt` with the raw draft+
  query, then route by the human's resume value (requires a checkpointer).

The worker that owns the review UI owns resuming via ``Command(resume=...)``.
Anything before ``interrupt`` re-runs on resume, so we keep side effects out of
this node (pure gate).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.types import Command, interrupt

from knowledge_engine.orchestration.state import WorkflowNode, WorkflowState
from knowledge_engine.schemas.extraction import GraphStatus

RENDER_NODE = "render"
END = "__end__"


class ReviewDecision(TypedDict, total=True):
    """Structured resume payload for the HITL gate."""

    approved: bool
    feedback: str | None


# (query, draft) -> decision. Keeps prod QADecider interchangeable w/ fakes.
ReviewGate = Callable[[str, str], Awaitable[ReviewDecision]]


def _auto_approve(_query: str, _draft: str) -> Awaitable[ReviewDecision]:
    """Headless default: always approve for automated/dev runs."""

    async def _decide() -> ReviewDecision:
        return ReviewDecision(approved=True, feedback=None)

    return _decide()


class ReviewNode(WorkflowNode):
    """Optional HITL review gate; auto-approves by default (no hardcoded UI)."""

    __slots__ = ("_gate", "interactive")

    def __init__(self, gate: ReviewGate | None = None, *, interactive: bool = False) -> None:
        self._gate = gate or _auto_approve
        self.interactive = interactive

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        draft = (state.get("notes") or [""])[-1]
        if self.interactive:
            # Pause until a human resumes with Command(resume={..}); interrupt is
            # the first thing we do so nothing before it re-runs on resume.
            payload = {
                "query": state.get("query", ""),
                "draft": draft,
                "action": "approve|reject",
            }
            decision = interrupt(payload)
        else:
            decision = await self._gate(state.get("query", ""), draft)
        if decision.get("approved"):
            return {
                "review_dir": "approved",
                "status": GraphStatus.DONE.value,
                "judge_critique": decision.get("feedback") or state.get("judge_critique"),
            }
        return {
            "review_dir": "rejected",
            "status": GraphStatus.FAILED.value,
            "judge_critique": decision.get("feedback") or "rejected in review",
        }


def review_route(state: WorkflowState) -> str:
    """Route: render on approval, terminate on rejection (Saga compensation)."""
    return RENDER_NODE if state.get("review_dir") == "approved" else END


__all__ = [
    "END",
    "RENDER_NODE",
    "ReviewDecision",
    "ReviewGate",
    "ReviewNode",
    "review_route",
    "Command",
]

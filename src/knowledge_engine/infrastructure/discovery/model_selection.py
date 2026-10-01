"""Model selection — pure policy for discovering a working free model.

Adapted from digest_agent's ``ModelSelectionPolicy`` into a small disjoint
state object (no framework dependency). Given a candidate pool and a sequence
of cheap health-check results, it decides: accept this model, keep testing the
next candidate, or terminate (exhausted / budget used).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from knowledge_engine.core.discovery import ModelId, TestResult

ACTION_PASS = "pass"
ACTION_CONTINUE = "continue"
ACTION_EXHAUSTED = "fail_exhausted"
ACTION_BUDGET = "fail_budget"


@dataclass
class SelectionState:
    """Mutable progress of the selection loop (pure; no I/O).

    Attributes:
        candidates: Attribute description.
        max_attempts: Attribute description.
        tried: Attribute description.
        attempts: Attribute description.
        selected: Attribute description.
    """

    candidates: list[ModelId]
    max_attempts: int = 5
    tried: set[ModelId] = field(default_factory=set)
    attempts: int = 0
    selected: ModelId | None = None

    @property
    def untried(self) -> list[ModelId]:
        """Ordered candidates not yet tested.

        Returns:
            list[ModelId]: Candidate model identifiers.

        """
        return [m for m in self.candidates if m not in self.tried]

    @property
    def exhausted(self) -> bool:
        """True when every candidate has been tested.

        Returns:
            bool: True on success.

        """
        return not self.untried

    @property
    def budget_exceeded(self) -> bool:
        """True when the attempt budget has been spent.

        Returns:
            bool: True on success.

        """
        return self.attempts >= self.max_attempts


@dataclass(frozen=True)
class SelectionDecision:
    """Outcome of processing one test result.

    Attributes:
        action: Attribute description.
        selected: Attribute description.
        reason: Attribute description.
    """

    action: str
    selected: ModelId | None = None
    reason: str = ""


class ModelSelectionPolicy:
    """Pure decision logic for the evaluator-optimizer model-selection loop."""

    @staticmethod
    def initial(state: SelectionState) -> SelectionDecision:
        """Return the action before any test is run.

        Args:
            state: Current workflow state.

        Returns:
            SelectionDecision: Selection decision.

        """
        if not state.candidates:
            return SelectionDecision(ACTION_EXHAUSTED, reason="no free models available")
        return SelectionDecision(ACTION_CONTINUE, reason="starting model selection")

    @staticmethod
    def on_test_result(state: SelectionState, result: TestResult) -> SelectionDecision:
        """Register a test result and decide whether to accept / try / stop.

        Args:
            state: Mutable selection progress (updated in place).
            result: Health-check result for the current candidate.

        Returns:
            SelectionDecision: Where the loop should go next.
        """
        state.tried.add(result.model_id)
        state.attempts += 1
        if result.passed:
            state.selected = result.model_id
            return SelectionDecision(ACTION_PASS, result.model_id, f"model {result.model_id} passed")
        if state.exhausted:
            return SelectionDecision(ACTION_EXHAUSTED, reason=f"tried all {len(state.candidates)} candidates")
        if state.budget_exceeded:
            return SelectionDecision(ACTION_BUDGET, reason=f"hit max_attempts={state.max_attempts}")
        return SelectionDecision(ACTION_CONTINUE, reason=f"{result.model_id} failed: {result.reason}")


__all__ = [
    "ACTION_BUDGET",
    "ACTION_CONTINUE",
    "ACTION_EXHAUSTED",
    "ACTION_PASS",
    "ModelSelectionPolicy",
    "SelectionDecision",
    "SelectionState",
]

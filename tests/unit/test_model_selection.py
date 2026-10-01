"""Unit tests: model selection policy (pure logic) + safe-test error paths."""

from __future__ import annotations

from knowledge_engine.core.discovery import TestResult
from knowledge_engine.infrastructure.discovery.model_selection import (
    ACTION_BUDGET,
    ACTION_CONTINUE,
    ACTION_EXHAUSTED,
    ACTION_PASS,
    ModelSelectionPolicy,
    SelectionState,
)


def test_initial_no_candidates_is_exhausted() -> None:
    state = SelectionState(candidates=[])
    assert ModelSelectionPolicy.initial(state).action == ACTION_EXHAUSTED


def test_initial_has_candidates_continues() -> None:
    state = SelectionState(candidates=["a", "b"])
    assert ModelSelectionPolicy.initial(state).action == ACTION_CONTINUE


def test_pass_selects_model() -> None:
    state = SelectionState(candidates=["a", "b"])
    decision = ModelSelectionPolicy.on_test_result(state, TestResult("a", True, "ok"))
    assert decision.action == ACTION_PASS
    assert decision.selected == "a"
    assert state.selected == "a"


def test_fail_continues_to_next() -> None:
    state = SelectionState(candidates=["a", "b"])
    decision = ModelSelectionPolicy.on_test_result(state, TestResult("a", False, "timeout"))
    assert decision.action == ACTION_CONTINUE
    assert state.untried == ["b"]


def test_all_tested_exhausts() -> None:
    state = SelectionState(candidates=["a", "b"])
    ModelSelectionPolicy.on_test_result(state, TestResult("a", False, "x"))
    decision = ModelSelectionPolicy.on_test_result(state, TestResult("b", False, "x"))
    assert decision.action == ACTION_EXHAUSTED


def test_budget_exceeded() -> None:
    state = SelectionState(candidates=["a", "b", "c"], max_attempts=1)
    ModelSelectionPolicy.on_test_result(state, TestResult("a", False, "x"))
    # Budget spent but candidates remain → the next failure reports budget-exceeded.
    decision = ModelSelectionPolicy.on_test_result(state, TestResult("b", False, "x"))
    assert decision.action == ACTION_BUDGET

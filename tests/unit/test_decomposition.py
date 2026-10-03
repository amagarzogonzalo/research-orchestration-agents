"""Unit tests: decomposition schemas + deterministic guardrails (TDD red)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge_engine.orchestration.guardrails import (
    budget_left,
    depth_allowed,
    rubric_assess,
    sanitize_query,
    should_stop,
)
from knowledge_engine.orchestration.routing import route_intent
from knowledge_engine.schemas.decomposition import (
    MAX_DECOMPOSE_DEPTH,
    MAX_DEEP_ROUNDS,
    MAX_SUBQUERIES,
    ResearchPlan,
    SubQuery,
)


def test_budgets_have_sane_bounds() -> None:
    assert MAX_DECOMPOSE_DEPTH == 2
    assert MAX_SUBQUERIES == 5
    assert MAX_DEEP_ROUNDS == 2


def test_subquery_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        SubQuery(query="q", source="carrier-pigeon")  # type: ignore[arg-type]


def test_plan_preserves_depth_tags() -> None:
    plan = ResearchPlan(
        sub_queries=[SubQuery(query="a", source="search", depth=1), SubQuery(query="b", source="mcp", depth=1)]
    )
    assert [s.depth for s in plan.sub_queries] == [1, 1]
    assert plan.sub_queries[1].source == "mcp"


def test_route_intent_override_wins() -> None:
    assert route_intent("hi", "deep") == "deep"
    assert route_intent("compare everything in depth", "chat") == "chat"


def test_route_intent_heuristics() -> None:
    assert route_intent("hi", None) == "chat"
    assert route_intent("what is the capital of France?", None) == "quick"
    assert route_intent("remember that I like pizza", None) == "memory"
    assert route_intent("compare LLM memory architectures, evaluation methods, and limitations", None) == "deep"
    assert route_intent("survey the evolution of retrieval-augmented generation with citations", None) == "deep"


def test_depth_allowed_respects_cap() -> None:
    assert depth_allowed(0) and depth_allowed(2)
    assert not depth_allowed(3)


def test_budget_left_counts_llm_calls() -> None:
    assert budget_left(0, 10)
    assert budget_left(9, 10)
    assert not budget_left(10, 10)


def test_sanitize_query_redacts_secrets() -> None:
    clean = sanitize_query("contact me at jane@example.com, key sk-abc123XYZ")
    assert "jane@example.com" not in clean
    assert "sk-abc123XYZ" not in clean
    assert "contact me" in clean


def test_rubric_empty_evidence_scores_low_with_gaps() -> None:
    score, gaps = rubric_assess(sources=[], bodies=[], notes=[])
    assert score < 0.5
    assert gaps


def test_rubric_rich_evidence_scores_high() -> None:
    score, gaps = rubric_assess(
        sources=[{"domain": "arxiv.org"}, {"domain": "nature.com"}, {"domain": "arxiv.org"}],
        bodies=[{"title": "a"}, {"title": "b"}, {"title": "c"}],
        notes=["finding one", "finding two"],
    )
    assert score >= 0.7
    assert gaps == []


def test_should_stop_on_score_rounds_or_budget() -> None:
    assert should_stop(score=0.9, gaps=[], depth=0, rounds=0, llm_calls=1)
    assert should_stop(score=0.2, gaps=["coverage"], depth=0, rounds=2, llm_calls=1)
    assert should_stop(score=0.2, gaps=["coverage"], depth=0, rounds=0, llm_calls=12)
    assert not should_stop(score=0.2, gaps=["coverage"], depth=0, rounds=0, llm_calls=1)

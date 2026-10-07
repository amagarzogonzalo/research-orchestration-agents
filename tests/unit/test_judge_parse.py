"""Unit tests: judge response parsing (offline, stubbed calls)."""

from __future__ import annotations

from typing import Any

from evals.lib import judge as jd


def _call(response_text: str):
    """Build a chat stub returning one canned message."""

    def call(**request: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": response_text}}]}

    return call


def test_score_uses_overall_when_present() -> None:
    """Classic shape (qwen): overall drives the verdict, detail preserved."""
    text = '{"criteria": [{"name": "c", "evidence": "e", "score": 4, "why": "w"}], "overall": 5}'
    res = jd.score("doc", "rubric", _call(text), "m", samples=1)
    assert res.scores == [5.0]
    assert res.median == 5.0
    assert res.detail[0]["criteria"][0]["name"] == "c"


def test_score_falls_back_to_criteria_mean_without_overall() -> None:
    """Some judges (gemma) return criteria but omit overall: use their mean."""
    text = '{"criteria": [{"name": "a", "score": 6}, {"name": "b", "score": 8}]}'
    res = jd.score("doc", "rubric", _call(text), "m", samples=1)
    assert res.scores == [7.0]
    assert len(res.detail) == 1


def test_score_ignores_non_numeric_criteria_in_fallback() -> None:
    """Non-numeric criterion scores never poison the fallback mean."""
    text = '{"criteria": [{"name": "a", "score": "high"}, {"name": "b", "score": 6}]}'
    res = jd.score("doc", "rubric", _call(text), "m", samples=1)
    assert res.scores == [6.0]


def test_score_empty_without_parseable_content() -> None:
    """Garbage or empty replies yield no scores (median 0.0), not a crash."""
    assert jd.score("doc", "rubric", _call("not json at all !!!"), "m", samples=1).scores == []
    assert jd.score("doc", "rubric", _call(""), "m", samples=1).median == 0.0
    assert jd.score("doc", "rubric", _call('{"criteria": []}'), "m", samples=1).scores == []

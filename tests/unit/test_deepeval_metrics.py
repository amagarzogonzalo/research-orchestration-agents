"""Unit tests: DeepEval mapping layer (offline, no model calls).

DeepEval itself is an opt-in extra; these tests importorskip it so the base
`make test` suite stays green without network, keys, or the extra installed.
"""

from __future__ import annotations

import pytest

try:
    from evals.lib.deepeval_judge import PinnedJudge
    from evals.lib.deepeval_metrics import METRICS, build_test_case, run_selected
except ImportError:
    PinnedJudge = METRICS = build_test_case = run_selected = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(PinnedJudge is None, reason="deepeval extra not installed")


def _sources() -> list[dict]:
    return [
        {"id": "openalex:W1", "title": "Paper One", "abstract": "Abstract one."},
        {"id": "doaj:9", "title": "Paper Nine", "abstract": ""},
    ]


def test_build_test_case_maps_artifact_and_sources() -> None:
    tc = build_test_case(
        query="What is RAG?",
        artifact="## Answer\n\nText [^openalex:W1].",
        sources=_sources(),
    )
    assert tc.input == "What is RAG?"
    assert "Text [^openalex:W1]" in tc.actual_output
    assert len(tc.retrieval_context) == 2
    assert "Paper One" in tc.retrieval_context[0]
    assert "Abstract one." in tc.retrieval_context[0]


def test_build_test_case_carries_optional_expected_output() -> None:
    tc = build_test_case(query="q", artifact="a", sources=[], expected="golden answer")
    assert tc.expected_output == "golden answer"


def test_build_test_case_without_expected_leaves_it_unset() -> None:
    tc = build_test_case(query="q", artifact="a", sources=[])
    assert tc.expected_output is None


def test_registry_pins_stable_metric_subset() -> None:
    assert set(METRICS) == {
        "answer_relevancy",
        "faithfulness",
        "contextual_precision",
        "contextual_recall",
        "task_completion",
        "step_efficiency",
    }
    for name, spec in METRICS.items():
        assert 0.0 < spec["threshold"] <= 1.0, name
        assert callable(spec["factory"]), name


def test_run_selected_reports_structured_results() -> None:
    class StubMetric:
        def __init__(self, score: float) -> None:
            self._score = score
            self.threshold = 0.5
            self.reason = "stubbed"

        def measure(self, _case) -> None:
            self.score = self._score

    tc = build_test_case(query="q", artifact="a", sources=[])
    results = run_selected(
        ["answer_relevancy"],
        tc,
        metric_factory=lambda name: StubMetric(0.9),
    )
    assert results == [
        {"name": "answer_relevancy", "score": 0.9, "threshold": 0.5, "passed": True, "reason": "stubbed"}
    ]


def test_run_selected_marks_sub_threshold_as_failed() -> None:
    class StubMetric:
        def __init__(self) -> None:
            self.threshold = 0.5
            self.reason = "stubbed-low"

        def measure(self, _case) -> None:
            self.score = 0.1

    tc = build_test_case(query="q", artifact="a", sources=[])
    (result,) = run_selected(["faithfulness"], tc, metric_factory=lambda name: StubMetric())
    assert result["passed"] is False
    assert result["score"] == 0.1


def test_pinned_judge_delegates_to_wrapped_client() -> None:
    calls: list[str] = []

    def fake_client(prompt: str) -> str:
        calls.append(prompt)
        return "judged!"

    judge = PinnedJudge(client=fake_client, model_name="test-model")
    assert judge.generate("hello?") == "judged!"
    assert calls == ["hello?"]
    assert judge.get_model_name() == "test-model"


def test_pinned_judge_async_generate_mirrors_sync() -> None:
    import asyncio

    judge = PinnedJudge(client=lambda prompt: "async-judged", model_name="m")

    async def go() -> str:
        return await judge.a_generate("ping?")

    assert asyncio.run(go()) == "async-judged"

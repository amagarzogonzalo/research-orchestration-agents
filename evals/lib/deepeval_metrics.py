"""Mapping between harness eval cases and DeepEval metrics.

Keeps a small, pinned subset of DeepEval metrics (RAG split + deep-workflow
trajectory) behind one registry, so case YAML stays declarative and the
default cassette-only path never imports a model. Thresholds live here next
to the metric choice and are calibrated from live runs, documented inline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
    StepEfficiencyMetric,
    TaskCompletionMetric,
)
from deepeval.test_case import LLMTestCase

METRICS: dict[str, dict[str, Any]] = {
    "answer_relevancy": {"factory": AnswerRelevancyMetric, "threshold": 0.7},
    "faithfulness": {"factory": FaithfulnessMetric, "threshold": 0.7},
    "contextual_precision": {"factory": ContextualPrecisionMetric, "threshold": 0.6},
    # contextual_recall needs a golden answer (expected_output); cases that
    # omit it simply don't select this metric.
    "contextual_recall": {"factory": ContextualRecallMetric, "threshold": 0.6},
    "task_completion": {"factory": TaskCompletionMetric, "threshold": 0.7},
    "step_efficiency": {"factory": StepEfficiencyMetric, "threshold": 0.6},
}


def build_test_case(
    query: str,
    artifact: str,
    sources: list[dict[str, Any]],
    expected: str | None = None,
) -> LLMTestCase:
    """Build a DeepEval test case from harness case data.

    Args:
        query: Research question (the case input).
        artifact: Rendered article text (the actual output).
        sources: Source records as plain dicts (the retrieval context).
        expected: Optional golden answer (enables recall-style metrics).

    Returns:
        LLMTestCase: Mapped test case.
    """
    context = []
    for source in sources:
        title = source.get("title", "")
        abstract = source.get("abstract", "")
        text = title if not abstract else f"{title}\n{abstract}"
        if text.strip():
            context.append(text)
    case = LLMTestCase(
        input=query,
        actual_output=artifact,
        retrieval_context=context,
    )
    if expected is not None:
        case.expected_output = expected
    return case


def run_selected(
    names: list[str],
    test_case: LLMTestCase,
    metric_factory: Callable[[str], Any] | None = None,
    model: Any | None = None,
) -> list[dict[str, Any]]:
    """Run named metrics against one test case and report structured results.

    Args:
        names: Registry keys from :data:`METRICS`.
        test_case: Mapped test case.
        metric_factory: Optional override ``(name) -> metric`` (tests inject
            stubs here so the suite needs no model).
        model: Judge model passed to real metric factories.

    Returns:
        list[dict[str, Any]]: One ``{name, score, threshold, passed, reason}``
            mapping per metric.
    """

    def default_factory(name: str) -> Any:
        spec = METRICS[name]
        kwargs: dict[str, Any] = {"threshold": spec["threshold"]}
        if model is not None:
            kwargs["model"] = model
        return spec["factory"](**kwargs)

    factory = metric_factory or default_factory
    results = []
    for name in names:
        metric = factory(name)
        metric.measure(test_case)
        threshold = float(getattr(metric, "threshold", 0.5))
        score = float(metric.score or 0.0)
        results.append(
            {
                "name": name,
                "score": score,
                "threshold": threshold,
                "passed": score >= threshold,
                "reason": str(getattr(metric, "reason", "") or ""),
            }
        )
    return results

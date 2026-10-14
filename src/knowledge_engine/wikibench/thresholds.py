"""Hard WikiBench thresholds and baseline-regression tolerance.

Two kinds of gate:

* **Hard thresholds** (``THRESHOLDS``) are absolute correctness floors a metric
  must satisfy on its own (duplicate rate 0, recall floor, no false positives).
* **Regression metrics** (``REGRESSION_METRICS``) are compared against the
  committed baseline: a drop is a regression even when still above its floor.
"""

from __future__ import annotations

#: ``metric key`` -> ``(operator, value)``. Keys are ``suite.metric``.
THRESHOLDS: dict[str, tuple[str, float]] = {
    "retrieval.dup_rate": ("<=", 0.0),
    "retrieval.recall@3": (">=", 0.9),
    "miss.fp_rate": ("<=", 0.05),
    "miss.exact_classify_acc": (">=", 1.0),
    "cost.zero_call_lift": (">", 0.0),
    "ingest.verdict_accuracy": (">=", 1.0),
    "ingest.idempotent_bytes_equal": (">=", 1.0),
    "ingest.index_drift": ("<=", 0.0),
    "ingest.dup_rate": ("<=", 0.0),
    "lint.clean_ok": (">=", 1.0),
    "lint.recall": (">=", 1.0),
    "robustness.injection_index_intact": (">=", 1.0),
    "robustness.index_complete": (">=", 1.0),
    "workflows.research_ok": (">=", 1.0),
    "workflows.qa_ok": (">=", 1.0),
    "workflows.summary_ok": (">=", 1.0),
    "workflows.deep_ok": (">=", 1.0),
}

#: Deterministic, higher-is-better metrics gated against the baseline.
#: Wall-clock latencies are deliberately excluded (environment-dependent).
REGRESSION_METRICS: tuple[str, ...] = (
    "retrieval.recall@1",
    "retrieval.recall@3",
    "retrieval.recall@5",
    "retrieval.mrr",
    "retrieval.ndcg@5",
    "retrieval.hybrid_recall@3",
    "workflows.research_supersteps",
    "workflows.qa_supersteps",
    "workflows.summary_supersteps",
    "workflows.deep_supersteps",
)

#: Slack allowed below a baseline value before it counts as a regression.
REGRESSION_TOLERANCE = 1e-9

__all__ = ["REGRESSION_METRICS", "REGRESSION_TOLERANCE", "THRESHOLDS"]

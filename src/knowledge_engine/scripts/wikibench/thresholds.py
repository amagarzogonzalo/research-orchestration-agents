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
    "a_retrieval.dup_rate": ("<=", 0.0),
    "a_retrieval.recall@3": (">=", 0.9),
    "b_miss.fp_rate": ("<=", 0.05),
    "b_miss.exact_classify_acc": (">=", 1.0),
    "c_compounding.zero_call_lift": (">", 0.0),
    "d_ingest.verdict_accuracy": (">=", 1.0),
    "d_ingest.idempotent_bytes_equal": (">=", 1.0),
    "d_ingest.index_drift": ("<=", 0.0),
    "d_ingest.dup_rate": ("<=", 0.0),
    "i_lint.clean_ok": (">=", 1.0),
    "i_lint.recall": (">=", 1.0),
    "j_robustness.injection_index_intact": (">=", 1.0),
    "j_robustness.index_complete": (">=", 1.0),
}

#: Deterministic, higher-is-better metrics gated against the baseline.
#: Wall-clock latencies are deliberately excluded (environment-dependent).
REGRESSION_METRICS: tuple[str, ...] = (
    "a_retrieval.recall@1",
    "a_retrieval.recall@3",
    "a_retrieval.recall@5",
    "a_retrieval.mrr",
    "a_retrieval.ndcg@5",
    "a_retrieval.hybrid_recall@3",
    "w_workflows.research_supersteps",
    "w_workflows.deep_supersteps",
)

#: Slack allowed below a baseline value before it counts as a regression.
REGRESSION_TOLERANCE = 1e-9

__all__ = ["REGRESSION_METRICS", "REGRESSION_TOLERANCE", "THRESHOLDS"]

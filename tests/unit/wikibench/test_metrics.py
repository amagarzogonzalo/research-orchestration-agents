"""Unit tests: WikiBench metric math (pure, deterministic)."""

from __future__ import annotations

import pytest

from knowledge_engine.wikibench import metrics


def test_recall_at_k_counts_gold_in_top_k() -> None:
    """Recall@k is the fraction of gold slugs found in the top k."""
    assert metrics.recall_at_k({"a", "b"}, ["a", "c", "b"], 1) == 0.5
    assert metrics.recall_at_k({"a", "b"}, ["a", "c", "b"], 3) == 1.0


def test_recall_at_k_empty_gold_is_vacuous_one() -> None:
    """A miss query with no gold is vacuously perfect."""
    assert metrics.recall_at_k(set(), ["x"], 3) == 1.0


def test_mrr_uses_first_relevant_rank() -> None:
    """MRR is 1/rank of the first relevant hit (0 when absent)."""
    assert metrics.mrr({"b"}, ["a", "b", "c"]) == pytest.approx(0.5)
    assert metrics.mrr({"z"}, ["a", "b"]) == 0.0


def test_ndcg_at_k_rewards_early_relevant_hits() -> None:
    """nDCG@k is 1.0 when the single gold item ranks first."""
    assert metrics.ndcg_at_k({"a"}, ["a", "b"], 5) == pytest.approx(1.0)
    early = metrics.ndcg_at_k({"a"}, ["a", "b"], 5)
    late = metrics.ndcg_at_k({"a"}, ["b", "a"], 5)
    assert early > late


def test_percentile_interpolates() -> None:
    """Percentile interpolates linearly between order statistics."""
    values = [10.0, 20.0, 30.0, 40.0]
    assert metrics.percentile(values, 50) == pytest.approx(25.0)
    assert metrics.percentile([], 95) == 0.0
    assert metrics.percentile([7.0], 95) == pytest.approx(7.0)


def test_duplicate_rate_flags_repeated_slugs_per_list() -> None:
    """duplicate_rate is the fraction of result lists containing a repeat."""
    assert metrics.duplicate_rate([["a", "b"], ["a", "a"]]) == pytest.approx(0.5)
    assert metrics.duplicate_rate([]) == 0.0


def test_mean_handles_empty() -> None:
    """mean returns 0.0 for an empty list."""
    assert metrics.mean([]) == 0.0
    assert metrics.mean([1.0, 3.0]) == pytest.approx(2.0)

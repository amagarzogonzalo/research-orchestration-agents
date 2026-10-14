"""Pure, deterministic metric math for WikiBench (no I/O, no state)."""

from __future__ import annotations

import math
from collections.abc import Iterable


def mean(values: Iterable[float]) -> float:
    """Arithmetic mean of a sequence (0.0 when empty).

    Args:
        values: Numeric values.

    Returns:
        float: The mean, or 0.0 for an empty sequence.
    """
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def recall_at_k(gold: set[str], ranked: list[str], k: int) -> float:
    """Fraction of gold slugs present in the top ``k`` ranked slugs.

    Args:
        gold: Relevant slug set (empty = a miss query; vacuously 1.0).
        ranked: Ranked slug list, best first.
        k: Cut-off rank.

    Returns:
        float: Recall in ``[0, 1]``.
    """
    if not gold:
        return 1.0
    found = len(set(ranked[:k]) & gold)
    return found / len(gold)


def mrr(gold: set[str], ranked: list[str]) -> float:
    """Mean reciprocal rank contribution: 1/rank of the first relevant hit.

    Args:
        gold: Relevant slug set.
        ranked: Ranked slug list, best first.

    Returns:
        float: ``1/rank`` of the first hit, or 0.0 when none is relevant.
    """
    for rank, slug in enumerate(ranked, start=1):
        if slug in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(gold: set[str], ranked: list[str], k: int) -> float:
    """Binary-relevance nDCG@k.

    Args:
        gold: Relevant slug set.
        ranked: Ranked slug list, best first.
        k: Cut-off rank.

    Returns:
        float: nDCG in ``[0, 1]`` (0.0 when there is no gold).
    """
    if not gold:
        return 0.0
    dcg = 0.0
    for rank, slug in enumerate(ranked[:k], start=1):
        if slug in gold:
            dcg += 1.0 / math.log2(rank + 1)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal > 0 else 0.0


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile of a sample.

    Args:
        values: Sample values (unsorted is fine).
        p: Percentile in ``[0, 100]``.

    Returns:
        float: The interpolated percentile, or 0.0 for an empty sample.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (p / 100.0) * (len(ordered) - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (idx - lower)


def duplicate_rate(result_lists: list[list[str]]) -> float:
    """Fraction of result lists that contain a repeated slug.

    Args:
        result_lists: One ranked slug list per query.

    Returns:
        float: Duplicate-list fraction in ``[0, 1]``.
    """
    if not result_lists:
        return 0.0
    dup = sum(1 for slugs in result_lists if len(slugs) != len(set(slugs)))
    return dup / len(result_lists)


__all__ = ["duplicate_rate", "mean", "mrr", "ndcg_at_k", "percentile", "recall_at_k"]

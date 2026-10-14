"""Shared helpers for WikiBench suites (article writing, retrieval eval)."""

from __future__ import annotations

import time
from pathlib import Path

from knowledge_engine.core.ports import WikiQueryPort
from knowledge_engine.scripts.wikibench.metrics import mean, mrr, ndcg_at_k, recall_at_k
from knowledge_engine.scripts.wikibench.schemas import QueryCase, QueryKind


def write_article(path: Path, text: str) -> str:
    """Write an article file and return its path as a string.

    Args:
        path: Destination path.
        text: Article markdown.

    Returns:
        str: The written path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


async def retrieval_eval(
    store: WikiQueryPort, queries: tuple[QueryCase, ...] | list[QueryCase], *, limit: int = 5
) -> tuple[dict[str, float], list[list[str]], list[float]]:
    """Score a store over every answerable query.

    Args:
        store: Wiki query port to evaluate.
        queries: Query cases (unanswerable ones are skipped).
        limit: Hits requested per query.

    Returns:
        tuple[dict[str, float], list[list[str]], list[float]]: Mean metrics,
        per-query ranked slug lists, and per-query wall seconds.
    """
    rec1: list[float] = []
    rec3: list[float] = []
    rec5: list[float] = []
    rr: list[float] = []
    ndcg: list[float] = []
    latencies: list[float] = []
    ranked_lists: list[list[str]] = []
    for case in queries:
        if case.kind is QueryKind.UNANSWERABLE:
            continue
        start = time.perf_counter()
        hits = await store.search_wiki(case.query, limit=limit)
        latencies.append(time.perf_counter() - start)
        ranked = [hit.slug for hit in hits]
        ranked_lists.append(ranked)
        gold = set(case.gold_slugs)
        rec1.append(recall_at_k(gold, ranked, 1))
        rec3.append(recall_at_k(gold, ranked, 3))
        rec5.append(recall_at_k(gold, ranked, 5))
        rr.append(mrr(gold, ranked))
        ndcg.append(ndcg_at_k(gold, ranked, 5))
    metrics = {
        "recall@1": mean(rec1),
        "recall@3": mean(rec3),
        "recall@5": mean(rec5),
        "mrr": mean(rr),
        "ndcg@5": mean(ndcg),
    }
    return metrics, ranked_lists, latencies


__all__ = ["retrieval_eval", "write_article"]

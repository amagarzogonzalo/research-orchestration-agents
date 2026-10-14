"""Suite A — retrieval quality: does the right page come back?

Recall@1/3/5, MRR, nDCG@5 plus the duplicate-page rate are measured for the
scan baseline and the hybrid (FTS5 BM25 + scan RRF) ranker on the same corpus.
Hybrid must never rank below scan, and one page must never occupy two slots.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.scripts.wikibench import metrics
from knowledge_engine.scripts.wikibench.corpus import build_corpus, seed_vault
from knowledge_engine.scripts.wikibench.schemas import SuiteResult
from knowledge_engine.scripts.wikibench.suites._common import retrieval_eval

_MIN_RECALL_AT_3 = 0.9


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite A and return its metrics.

    Args:
        workspace: Scratch directory for the two seeded vaults.
        scale: Corpus size (number of pages).
        seed: Corpus seed.
        backend: Backend id (``file`` for Tier 0).

    Returns:
        SuiteResult: Retrieval metrics and any threshold findings.
    """
    built = build_corpus(scale, seed)
    scan_root = workspace / "a_scan"
    hybrid_root = workspace / "a_hybrid"
    seed_vault(scan_root, built, sidecar=False)
    seed_vault(hybrid_root, built, sidecar=True)
    scan_metrics, _, _ = await retrieval_eval(FileWikiStore(scan_root), built.queries)
    hybrid_metrics, hybrid_lists, latencies = await retrieval_eval(FileWikiStore(hybrid_root), built.queries)
    dup = metrics.duplicate_rate(hybrid_lists)
    measured = {
        "recall@1": hybrid_metrics["recall@1"],
        "recall@3": hybrid_metrics["recall@3"],
        "recall@5": hybrid_metrics["recall@5"],
        "mrr": hybrid_metrics["mrr"],
        "ndcg@5": hybrid_metrics["ndcg@5"],
        "dup_rate": dup,
        "latency_p50_ms": metrics.percentile(latencies, 50) * 1000.0,
        "latency_p95_ms": metrics.percentile(latencies, 95) * 1000.0,
        "scan_recall@3": scan_metrics["recall@3"],
        "hybrid_recall@3": hybrid_metrics["recall@3"],
    }
    findings: list[str] = []
    if dup > 0.0:
        findings.append(f"duplicate-page rate {dup:.3f} (must be 0)")
    if hybrid_metrics["recall@3"] + 1e-9 < scan_metrics["recall@3"]:
        findings.append(f"hybrid recall@3 {hybrid_metrics['recall@3']:.3f} < scan {scan_metrics['recall@3']:.3f}")
    if hybrid_metrics["recall@3"] < _MIN_RECALL_AT_3:
        findings.append(f"recall@3 {hybrid_metrics['recall@3']:.3f} < {_MIN_RECALL_AT_3}")
    return SuiteResult(
        name="a_retrieval",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

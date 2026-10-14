"""Use case: answer from the wiki.

Two suites: does the right page come back (``retrieval``), and does the wiki
stay quiet on unrelated queries while classifying exact vs scored hits safely
(``miss``). Together they cover the "ask the wiki without going live" path.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.wikibench import metrics
from knowledge_engine.wikibench.corpus import build_corpus, seed_vault
from knowledge_engine.wikibench.schemas import QueryKind, SuiteResult
from knowledge_engine.wikibench.use_cases._common import retrieval_eval

_USE_CASE = "answer-from-wiki"
_MIN_RECALL_AT_3 = 0.9
_MAX_FP_RATE = 0.05


async def retrieval(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run the retrieval-quality suite (scan baseline vs hybrid ranker).

    Args:
        workspace: Scratch directory for the two seeded vaults.
        scale: Corpus size (number of pages).
        seed: Corpus seed.
        backend: Backend id.

    Returns:
        SuiteResult: Retrieval metrics and any threshold findings.
    """
    built = build_corpus(scale, seed)
    scan_root = workspace / "scan"
    hybrid_root = workspace / "hybrid"
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
        name="retrieval",
        use_case=_USE_CASE,
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


async def miss(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run the miss-discipline suite.

    Args:
        workspace: Scratch directory for the seeded vault.
        scale: Corpus size (number of pages).
        seed: Corpus seed.
        backend: Backend id.

    Returns:
        SuiteResult: Miss-discipline metrics and threshold findings.
    """
    built = build_corpus(scale, seed)
    root = workspace / "miss"
    seed_vault(root, built)
    store = FileWikiStore(root)

    unanswerable = [q for q in built.queries if q.kind is QueryKind.UNANSWERABLE]
    false_positives = 0
    for case in unanswerable:
        if await store.search_wiki(case.query, limit=5):
            false_positives += 1
    fp_rate = false_positives / len(unanswerable) if unanswerable else 0.0

    answerable = [q for q in built.queries if q.kind is not QueryKind.UNANSWERABLE]
    page_shas = {common.sha256_text(page.title) for page in built.pages}
    correct = 0
    for case in answerable:
        query_sha = common.sha256_text(case.query)
        expected_exact = query_sha in page_shas
        hits = await store.search_wiki(case.query, limit=5)
        observed_exact = any(hit.query_sha256 == query_sha for hit in hits)
        correct += int(expected_exact == observed_exact)
    accuracy = correct / len(answerable) if answerable else 0.0

    measured = {
        "fp_rate": fp_rate,
        "miss_pass_rate": 1.0 - fp_rate,
        "exact_classify_acc": accuracy,
    }
    findings: list[str] = []
    if fp_rate > _MAX_FP_RATE:
        findings.append(f"false-positive rate {fp_rate:.3f} > {_MAX_FP_RATE}")
    if accuracy < 1.0:
        findings.append(f"exact/scored classification accuracy {accuracy:.3f} < 1.0")
    return SuiteResult(
        name="miss",
        use_case=_USE_CASE,
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["miss", "retrieval"]

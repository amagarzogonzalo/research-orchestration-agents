"""Suite B — miss discipline: does the wiki stay quiet when it should?

Unrelated queries must return nothing (a false positive is the failure mode),
and the store's exact-hit signal (``query_sha256`` matches the query) must
classify exact vs scored-only queries correctly so routing stays safe.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.scripts.wikibench.corpus import build_corpus, seed_vault
from knowledge_engine.scripts.wikibench.schemas import QueryKind, SuiteResult

_MAX_FP_RATE = 0.05


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite B and return its metrics.

    Args:
        workspace: Scratch directory for the seeded vault.
        scale: Corpus size (number of pages).
        seed: Corpus seed.
        backend: Backend id.

    Returns:
        SuiteResult: Miss-discipline metrics and threshold findings.
    """
    built = build_corpus(scale, seed)
    root = workspace / "b_miss"
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
        name="b_miss",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

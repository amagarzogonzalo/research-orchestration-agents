"""Suite D — ingest integrity: verdicts, dedup, drift and idempotent replay.

Checks the New/No-material/Update verdicts on labelled article pairs, that a
repeated ingest leaves page + index bytes unchanged, that every filed page is
indexed (zero drift) and that no slug is duplicated.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.schemas.wiki import WikiVerdict
from knowledge_engine.scripts.wikibench.metrics import mean
from knowledge_engine.scripts.wikibench.schemas import SuiteResult
from knowledge_engine.scripts.wikibench.suites._common import write_article

_PAD = "Padding sentence keeps the page above the thin threshold. "
_V1 = "# Alpha fact\n\nThe alpha fact value is 1. " + _PAD * 20
_V2 = "# Alpha fact\n\nThe alpha fact value is 2 (updated). " + _PAD * 20
_BETA = "# Beta topic\n\nAn unrelated beta topic. " + _PAD * 20
_GAMMA = "# Gamma fact\n\nThe gamma fact value is stable. " + _PAD * 20

_IDEMPOTENT_REPLAYS = 10


def _snapshot(root: Path) -> dict[str, str]:
    """Capture content bytes that a no-material replay must not change.

    The append-only ``log.md`` is intentionally excluded: a no-material replay
    logs by design (Karpathy convention), so only pages, the index, the
    overview and the cache are the byte-stability surface.

    Args:
        root: Wiki root.

    Returns:
        dict[str, str]: Relative path -> file text, for stable content files.
    """
    snap: dict[str, str] = {}
    for pattern in ("*.md", "sources/*.md", "synthesis/*.md", "queries/*.md", common.CACHE_NAME):
        for path in sorted(root.glob(pattern)):
            if path.name == common.LOG_NAME:
                continue
            snap[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return snap


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite D and return its integrity metrics.

    Args:
        workspace: Scratch directory for the wiki and its articles.
        scale: Corpus size (unused; suite D uses fixed labelled pairs).
        seed: Corpus seed (unused).
        backend: Backend id.

    Returns:
        SuiteResult: Ingest-integrity metrics and threshold findings.
    """
    store = FileWikiStore(workspace / "d_ingest")
    articles = workspace / "d_articles"

    first = await store.ingest("t1", "alpha fact", write_article(articles / "v1.md", _V1))
    same_thread = await store.ingest("t1", "alpha fact", write_article(articles / "v1b.md", _V1))
    cross_thread = await store.ingest("t2", "alpha fact", write_article(articles / "v1c.md", _V1))
    updated = await store.ingest("t3", "alpha fact", write_article(articles / "v2.md", _V2))
    unrelated = await store.ingest("t4", "beta topic", write_article(articles / "b.md", _BETA))
    expected = [WikiVerdict.NEW, WikiVerdict.NO_MATERIAL, WikiVerdict.NO_MATERIAL, WikiVerdict.UPDATE, WikiVerdict.NEW]
    actual = [r.verdict for r in (first, same_thread, cross_thread, updated, unrelated)]
    verdict_accuracy = mean([float(a is e) for a, e in zip(actual, expected, strict=True)])

    gamma = write_article(articles / "g.md", _GAMMA)
    await store.ingest("t5", "gamma fact", gamma)
    before = _snapshot(store.root)
    for _ in range(_IDEMPOTENT_REPLAYS - 1):
        await store.ingest("t5", "gamma fact", gamma)
    idempotent = 1.0 if _snapshot(store.root) == before else 0.0

    files = list((store.root / "sources").glob("*.md"))
    stems = [p.stem for p in files]
    dup_rate = (len(stems) - len(set(stems))) / len(stems) if stems else 0.0
    index_text = (store.root / common.INDEX_NAME).read_text(encoding="utf-8")
    indexed = {stem for stem in stems if f"/{stem}.md)" in index_text}
    drift = float(len(set(stems) - indexed))

    measured = {
        "verdict_accuracy": verdict_accuracy,
        "idempotent_bytes_equal": idempotent,
        "index_drift": drift,
        "dup_rate": dup_rate,
    }
    findings: list[str] = []
    if verdict_accuracy < 1.0:
        findings.append(f"verdict accuracy {verdict_accuracy:.3f}: {actual}")
    if idempotent < 1.0:
        findings.append("no-material replay changed page/index bytes")
    if drift != 0.0:
        findings.append(f"index drift {drift:.0f} pages missing from the index")
    if dup_rate != 0.0:
        findings.append(f"duplicate-page rate {dup_rate:.3f}")
    return SuiteResult(
        name="d_ingest",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

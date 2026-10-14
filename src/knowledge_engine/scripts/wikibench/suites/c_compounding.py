"""Suite C — cost compounding: does the wiki get cheaper over time?

Replays a 200-query stream (each fact filed once, then repeated) against an
empty wiki. A repeat that exact-hits spends zero live calls; the rise of the
zero-live-call rate over the stream is the compounding curve (the headline
chart). Live calls are simulated by ingest — no network is touched.
"""

from __future__ import annotations

import random
from pathlib import Path

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.scripts.wikibench.corpus import build_corpus
from knowledge_engine.scripts.wikibench.schemas import SuiteResult
from knowledge_engine.scripts.wikibench.suites._common import write_article

_STREAM_LEN = 200
_FILLER = "Live research findings for the query. "


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite C and return its compounding metrics.

    Args:
        workspace: Scratch directory for the wiki and its live results.
        scale: Number of distinct facts (corpus pages).
        seed: Corpus/stream seed.
        backend: Backend id.

    Returns:
        SuiteResult: Compounding metrics and threshold findings.
    """
    built = build_corpus(scale, seed)
    store = FileWikiStore(workspace / "c_compounding")
    articles = workspace / "c_articles"
    pool = [page.title for page in built.pages]
    rng = random.Random(seed)
    stream = [pool[i] if i < len(pool) else pool[rng.randrange(len(pool))] for i in range(_STREAM_LEN)]

    zero_calls = 0
    live_calls = 0
    zero_call_rates: list[float] = []
    for i, query in enumerate(stream):
        query_sha = common.sha256_text(query)
        hits = await store.search_wiki(query, limit=1)
        if any(hit.query_sha256 == query_sha for hit in hits):
            zero_calls += 1
        else:
            live_calls += 1
            text = f"# {query}\n\n{_FILLER * 4}\n"
            article = write_article(articles / f"{i:04d}.md", text)
            await store.ingest(f"c{i:04d}", query, article)
        zero_call_rates.append(zero_calls / (i + 1))

    measured = {
        "zero_call_rate_final": zero_call_rates[-1],
        "zero_call_lift": zero_call_rates[-1] - zero_call_rates[0],
        "live_calls_per_100": live_calls / _STREAM_LEN * 100.0,
        "exact_hits": float(zero_calls),
    }
    findings: list[str] = []
    if measured["zero_call_lift"] <= 0.0:
        findings.append("zero-live-call rate did not rise over the stream (no compounding)")
    return SuiteResult(
        name="c_compounding",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

#!/usr/bin/env python3
"""Clean-report: measure source duplication classes across the research store.

Evidence tool for Loop 2 (per docs/LLM_WIKI.md): scans
``research_store/*/sources.json`` batches and reports the measured share of
each duplication class, so dedup flags (``dedup_doi``/``near_dedup``) are
enabled by measurement, never by taste.

    uv run python bin/clean_report.py [ROOT]   # default: research_store
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "src")

from knowledge_engine.infrastructure.clean.cleaner import SourceCleaner
from knowledge_engine.schemas.source import SourceRecord


async def report(root: Path) -> int:
    """Scan every sources.json under root and print class-share findings.

    Args:
        root: Store root containing ``<thread>/sources.json`` folders.

    Returns:
        int: Process exit code (0 always; findings are advisory).
    """
    batches = sorted(root.rglob("sources.json"))
    total_records = 0
    exact_id = 0
    same_doi = 0
    near_pairs = 0
    near_dropped = 0
    thread_dois: dict[str, set[str]] = {}
    cleaner = SourceCleaner(near_dedup=True)

    for path in batches:
        thread = path.parent.name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! unreadable: {path} ({exc})")
            continue
        records = [SourceRecord.model_validate(item) for item in payload if isinstance(item, dict)]
        total_records += len(records)
        ids = [r.id for r in records]
        exact_id += len(ids) - len(set(ids))
        dois = {r.doi for r in records if r.doi}
        thread_dois[thread] = dois
        same_doi += len(dois) - len({r.doi for r in records if r.doi})
        result = await cleaner.clean(records)
        near_pairs += result.clusters
        near_dropped += result.near_drops + result.containment_drops

    seen_threads: Counter[str] = Counter()
    for dois in thread_dois.values():
        for doi in dois:
            seen_threads[doi] += 1
    cross_file = sum(1 for count in seen_threads.values() if count > 1)

    print(f"batches scanned       : {len(batches)}")
    print(f"records total         : {total_records}")
    print(f"exact-id dupes        : {exact_id}")
    print(f"same-doi rows         : {same_doi} (distinct ids, same DOI)")
    print(f"near-dup clusters     : {near_pairs}")
    print(f"near/containment drops: {near_dropped}")
    print(f"cross-thread DOIs     : {cross_file} (papers filed under >1 thread)")
    verdict = "enable flags" if (same_doi or near_dropped) else "keep flags OFF (no measured duplication)"
    print(f"decision              : {verdict}")
    return 0


def main() -> int:
    """Entry point.

    Returns:
        int: Exit code.
    """
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research_store")
    if not root.is_dir():
        print(f"store root not found: {root}")
        return 1
    return asyncio.run(report(root))


if __name__ == "__main__":
    raise SystemExit(main())

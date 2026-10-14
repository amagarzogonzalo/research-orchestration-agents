"""Retention: prune old run artifacts so the free Supabase tier (500 MB) holds.

The service writes two kinds of cloud data:

* **Storage buckets** — ``bodies`` (raw scraped markdown), ``articles`` and
  ``sources`` (per-run derivatives). These dominate volume and are disposable
  once a run is older than the retention window.
* **Postgres** — checkpoints, the cross-thread store, ``service_jobs`` and the
  relational wiki. The wiki is *curated knowledge* and is never pruned here.

Policy: delete Storage objects older than ``--days`` (default 30), but always
keep the ``--keep-runs`` most recently updated run prefixes regardless of age.
Dry-run by default; ``--apply`` performs the deletions. Reports usage before and
after (per bucket, bytes + objects) so the effect on the 500 MB budget is
explicit.

Commands::

    uv run python -m knowledge_engine.scripts.retention --dry-run
    uv run python -m knowledge_engine.scripts.retention --apply --days 30 --keep-runs 5
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

DEFAULT_BUCKETS = ("bodies", "articles", "sources")
_WIKI_BUCKET = "wiki"
_DELETE_CHUNK = 100


@dataclass(frozen=True)
class StoredObject:
    """One object in a Storage bucket.

    Attributes:
        bucket: Bucket name.
        name: Object key (path within the bucket).
        size: Object size in bytes.
        updated_at: Last-modified time as an epoch timestamp.
    """

    bucket: str
    name: str
    size: int
    updated_at: float


class ObjectStore(Protocol):
    """Minimal object-store surface retention needs."""

    def list_objects(self, buckets: tuple[str, ...]) -> list[StoredObject]:
        """List objects across buckets.

        Args:
            buckets: Bucket names.

        Returns:
            list[StoredObject]: Objects found.
        """
        ...

    def delete_objects(self, objects: list[StoredObject]) -> int:
        """Delete the given objects.

        Args:
            objects: Objects to delete.

        Returns:
            int: Number of objects deleted.
        """
        ...


def _prefix(name: str) -> str:
    """Return a run prefix (the first path segment).

    Args:
        name: Object key.

    Returns:
        str: Run prefix.
    """
    return name.split("/", 1)[0]


def _parse_ts(value: str | None) -> float:
    """Parse an ISO-8601 timestamp to epoch seconds (unknown -> now = keep).

    Args:
        value: Timestamp string (e.g. ``2026-10-14T15:00:00Z``).

    Returns:
        float: Epoch seconds; the current time when unparseable.
    """
    if not value:
        return time.time()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def summarize(objects: list[StoredObject]) -> dict[str, dict[str, int]]:
    """Per-bucket object counts and byte totals.

    Args:
        objects: Objects to summarize.

    Returns:
        dict[str, dict[str, int]]: ``bucket -> {"count", "bytes"}``.
    """
    out: dict[str, dict[str, int]] = {}
    for obj in objects:
        slot = out.setdefault(obj.bucket, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += obj.size
    return out


def plan_retention(objects: list[StoredObject], *, cutoff: float, keep_runs: int = 0) -> list[StoredObject]:
    """Select objects to delete: older than ``cutoff``, minus kept runs.

    Args:
        objects: All objects under consideration.
        cutoff: Epoch seconds; objects updated at/after this are kept.
        keep_runs: Always keep the N most recently updated run prefixes.

    Returns:
        list[StoredObject]: Objects to delete.
    """
    kept: set[str] = set()
    if keep_runs > 0:
        newest: dict[str, float] = {}
        for obj in objects:
            prefix = _prefix(obj.name)
            newest[prefix] = max(newest.get(prefix, 0.0), obj.updated_at)
        kept = {prefix for prefix, _ in sorted(newest.items(), key=lambda kv: kv[1], reverse=True)[:keep_runs]}
    return [obj for obj in objects if obj.updated_at < cutoff and _prefix(obj.name) not in kept]


class LocalObjectStore:
    """Directory-backed store (offline tests; mirrors the bucket layout).

    Args:
        root: Root directory holding ``<bucket>/<object>`` files.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def list_objects(self, buckets: tuple[str, ...]) -> list[StoredObject]:
        """List files under each bucket directory.

        Args:
            buckets: Bucket names.

        Returns:
            list[StoredObject]: Files found.
        """
        out: list[StoredObject] = []
        for bucket in buckets:
            base = self._root / bucket
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    name = path.relative_to(base).as_posix()
                    out.append(StoredObject(bucket, name, stat.st_size, stat.st_mtime))
        return out

    def delete_objects(self, objects: list[StoredObject]) -> int:
        """Delete the selected files.

        Args:
            objects: Objects to delete.

        Returns:
            int: Number deleted.
        """
        count = 0
        for obj in objects:
            path = self._root / obj.bucket / obj.name
            if path.is_file():
                path.unlink()
                count += 1
        return count


class SupabaseObjectStore:
    """Supabase Storage-backed store (production).

    Args:
        client: A Supabase client (from :meth:`from_env`).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> SupabaseObjectStore:
        """Build from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

        Returns:
            SupabaseObjectStore: Configured store.
        """
        from supabase import create_client

        return cls(create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]))

    def _walk(self, bucket: str, prefix: str, out: list[StoredObject]) -> None:
        """Recursively list one bucket (folders appear as entries with no id).

        Args:
            bucket: Bucket name.
            prefix: Current folder prefix.
            out: Accumulator.
        """
        try:
            entries = self._client.storage.from_(bucket).list(prefix or None, {"limit": 1000})
        except Exception:  # noqa: BLE001 - a missing/empty bucket lists as nothing
            return
        for entry in entries or []:
            name = str(entry.get("name", ""))
            full = f"{prefix}/{name}" if prefix else name
            if entry.get("id") is None:
                self._walk(bucket, full, out)
            else:
                meta = entry.get("metadata") or {}
                size = int(meta.get("size") or entry.get("size") or 0)
                out.append(StoredObject(bucket, full, size, _parse_ts(entry.get("updated_at"))))

    def list_objects(self, buckets: tuple[str, ...]) -> list[StoredObject]:
        """Recursively list objects across buckets.

        Args:
            buckets: Bucket names.

        Returns:
            list[StoredObject]: Objects found.
        """
        out: list[StoredObject] = []
        for bucket in buckets:
            self._walk(bucket, "", out)
        return out

    def delete_objects(self, objects: list[StoredObject]) -> int:
        """Delete objects, chunked per bucket.

        Args:
            objects: Objects to delete.

        Returns:
            int: Number requested for deletion.
        """
        by_bucket: dict[str, list[str]] = {}
        for obj in objects:
            by_bucket.setdefault(obj.bucket, []).append(obj.name)
        count = 0
        for bucket, names in by_bucket.items():
            for start in range(0, len(names), _DELETE_CHUNK):
                chunk = names[start : start + _DELETE_CHUNK]
                self._client.storage.from_(bucket).remove(chunk)
                count += len(chunk)
        return count


def _postgres_size_mb() -> float | None:
    """Return the Postgres database size in MB, or None when unavailable.

    Returns:
        float | None: Size in MB.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return None
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=15) as conn:
            row = conn.execute("select pg_database_size(current_database())").fetchone()
        return round(int(row[0]) / (1024 * 1024), 2) if row else None
    except Exception:  # noqa: BLE001 - best-effort reporting only
        return None


def _print_report(title: str, usage: dict[str, dict[str, int]]) -> None:
    """Print a per-bucket usage table.

    Args:
        title: Section title.
        usage: ``bucket -> {"count", "bytes"}``.
    """
    total_bytes = sum(v["bytes"] for v in usage.values())
    total_count = sum(v["count"] for v in usage.values())
    print(f"\n{title}")
    for bucket in sorted(usage):
        slot = usage[bucket]
        print(f"  {bucket:<10} {slot['count']:>6} objects  {slot['bytes'] / 1e6:>9.2f} MB")
    print(f"  {'TOTAL':<10} {total_count:>6} objects  {total_bytes / 1e6:>9.2f} MB")


def main(argv: list[str] | None = None) -> int:
    """CLI entry: plan (and optionally apply) Storage retention.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(description="prune old run artifacts (Supabase free tier)")
    parser.add_argument("--days", type=int, default=30, help="retention window in days")
    parser.add_argument("--keep-runs", type=int, default=5, help="always keep the N newest runs")
    parser.add_argument("--buckets", default=",".join(DEFAULT_BUCKETS), help="comma-separated buckets")
    parser.add_argument("--include-wiki", action="store_true", help="also prune the wiki bucket (off by default)")
    parser.add_argument("--apply", action="store_true", help="perform deletions (default: dry-run)")
    parser.add_argument("--root", default=None, help="local store root instead of Supabase (tests)")
    parser.add_argument("--report", default=None, help="write a JSON before/after report here")
    args = parser.parse_args(argv)

    buckets = tuple(b.strip() for b in args.buckets.split(",") if b.strip())
    if args.include_wiki:
        buckets = (*buckets, _WIKI_BUCKET)

    store: ObjectStore
    if args.root:
        store = LocalObjectStore(Path(args.root))
    else:
        from knowledge_engine.config import load_dotenv

        load_dotenv()
        store = SupabaseObjectStore.from_env()

    before = store.list_objects(buckets)
    before_usage = summarize(before)
    _print_report("BEFORE", before_usage)
    print(f"  postgres: {_postgres_size_mb()} MB")

    cutoff = time.time() - args.days * 86400
    to_delete = plan_retention(before, cutoff=cutoff, keep_runs=args.keep_runs)
    delete_usage = summarize(to_delete)
    _print_report(f"PLANNED DELETION (>{args.days}d, keep {args.keep_runs} runs)", delete_usage)

    deleted = 0
    after_usage: dict[str, dict[str, int]] = before_usage
    if args.apply and to_delete:
        deleted = store.delete_objects(to_delete)
        after_usage = summarize(store.list_objects(buckets))
        _print_report("AFTER", after_usage)
    print(f"\n{'applied' if args.apply else 'dry-run'}: {len(to_delete)} objects planned, {deleted} deleted")

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "days": args.days,
                    "keep_runs": args.keep_runs,
                    "buckets": list(buckets),
                    "applied": args.apply,
                    "before": before_usage,
                    "planned": delete_usage,
                    "after": after_usage,
                    "deleted": deleted,
                    "objects": [asdict(o) for o in to_delete],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"report written: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

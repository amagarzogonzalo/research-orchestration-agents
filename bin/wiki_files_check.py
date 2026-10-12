"""Read-only wiki-files health check (``make wiki-files-check``).

Reports:
  * v_unreferenced_files  — files rows nothing references
  * wiki.files rows whose Storage object is missing
  * v_sources_missing_body — sources whose body was never stored

Read-only. Re-hashes a sample (5) of present local objects and updates their
``verified_at``. Deletion happens only behind an explicit --delete-orphans flag
(which this tool does NOT implement by default).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


def _connect() -> Any:
    """Open a psycopg connection from DATABASE_URL/WIKI_TEST_PG_DSN.

    Returns:
        Any: A psycopg connection.

    Raises:
        SystemExit: When no DSN is configured.
    """
    try:
        import psycopg
    except ImportError:
        sys.exit("wiki-files-check needs psycopg (the 'store' extra): uv sync --extra store")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("WIKI_TEST_PG_DSN")
    if not dsn:
        sys.exit("set DATABASE_URL or WIKI_TEST_PG_DSN")
    return psycopg.connect(dsn, autocommit=True)


def main(argv: list[str] | None = None) -> int:
    """Run the read-only files health check.

    Args:
        argv: CLI args.

    Returns:
        int: Exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bodies-root",
        default=os.environ.get("WIKI_BODIES_ROOT", "research_store/bodies"),
        help="Local bodies root when using a filesystem FileStore (default: research_store/bodies).",
    )
    parser.add_argument("--delete-orphans", action="store_true", help="(unimplemented) delete orphan objects/files.")
    args = parser.parse_args(argv)

    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM wiki.files")
        total = cur.fetchone()[0]

        cur.execute("SELECT sha256, bucket, path FROM wiki.v_unreferenced_files ORDER BY created_at")
        unreferenced = cur.fetchall()

        # files rows whose object is missing from the filesystem store
        missing: list[tuple[str, str]] = []
        cur.execute("SELECT sha256, bucket, path FROM wiki.files ORDER BY created_at")
        for sha, bucket, path in cur.fetchall():
            obj = Path(args.bodies_root) / bucket / path
            if not obj.is_file():
                missing.append((sha, path))

        cur.execute("SELECT id, kind, external_id, url FROM wiki.v_sources_missing_body ORDER BY id")
        missing_bodies = cur.fetchall()

        # re-hash a sample of present objects and update verified_at
        rehashed = 0
        cur.execute("SELECT sha256, bucket, path FROM wiki.files ORDER BY random() LIMIT 5")
        for sha, bucket, path in cur.fetchall():
            obj = Path(args.bodies_root) / bucket / path
            if obj.is_file():
                import hashlib

                digest = hashlib.sha256(obj.read_bytes()).hexdigest()
                if digest == sha:
                    cur.execute("UPDATE wiki.files SET verified_at = now() WHERE sha256 = %s", (sha,))
                    rehashed += 1

    print(f"files rows: {total}")
    print(f"unreferenced files: {len(unreferenced)}")
    for sha, bucket, path in unreferenced:
        print(f"  - {bucket}/{path} ({sha[:12]})")
    print(f"files rows missing object: {len(missing)}")
    for sha, path in missing:
        print(f"  - {path} ({sha[:12]})")
    print(f"sources missing body: {len(missing_bodies)}")
    print(f"sample re-hashed & verified: {rehashed}")

    if args.delete_orphans:
        print("NOTE: --delete-orphans is not implemented; no objects were deleted (read-only by default).")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

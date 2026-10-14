"""Back up a Supabase Storage bucket to a local directory (durability net).

Supabase Free has no point-in-time recovery, so a scheduled dump of the curated
``wiki`` bucket to a GitHub Actions artifact is a free backup. Objects are
written preserving their key path.

Commands::

    uv run python -m knowledge_engine.scripts.backup_wiki --out wiki-backup
    uv run python -m knowledge_engine.scripts.backup_wiki --bucket wiki --out wiki-backup
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any


class SupabaseBucketBackup:
    """Recursively download every object in a bucket to a directory.

    Args:
        client: A Supabase client (from :meth:`from_env`).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> SupabaseBucketBackup:
        """Build from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

        Returns:
            SupabaseBucketBackup: Configured backup helper.
        """
        from supabase import create_client

        return cls(create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]))

    def _walk(self, bucket: str, prefix: str, out: list[str]) -> None:
        """Recursively collect object keys (folders have no ``id``).

        Args:
            bucket: Bucket name.
            prefix: Current folder prefix.
            out: Accumulator of object keys.
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
                out.append(full)

    def backup(self, bucket: str, out_dir: Path) -> int:
        """Download every object in ``bucket`` under ``out_dir``.

        Args:
            bucket: Bucket name.
            out_dir: Destination directory (created on demand).

        Returns:
            int: Number of objects written.
        """
        keys: list[str] = []
        self._walk(bucket, "", keys)
        for key in keys:
            data = self._client.storage.from_(bucket).download(key)
            target = out_dir / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else str(data).encode("utf-8"))
        return len(keys)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: back up a bucket to a local directory.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(description="back up a Supabase Storage bucket")
    parser.add_argument("--bucket", default="wiki", help="bucket to back up")
    parser.add_argument("--out", default="wiki-backup", help="output directory")
    args = parser.parse_args(argv)

    from knowledge_engine.config import load_dotenv

    load_dotenv()
    count = SupabaseBucketBackup.from_env().backup(args.bucket, Path(args.out))
    print(f"backed up {count} object(s) from '{args.bucket}' to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

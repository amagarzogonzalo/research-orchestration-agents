#!/usr/bin/env python3
"""Migrate file-checkpointer threads to Supabase Postgres (W7, one-shot).

Reads every thread under ``research_store/checkpoints/`` and replays its
checkpoints + writes into ``AsyncPostgresSaver`` (same LangGraph API; needs
the ``store`` extra and a session-pooler ``DATABASE_URL``). Files are left in
place as the cold backup — this script never deletes.

    uv run python -m knowledge_engine.scripts.migrate_checkpoints --dry-run
    uv run python -m knowledge_engine.scripts.migrate_checkpoints          # writes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import cast

if os.name == "nt":
    # psycopg async on Windows needs the selector loop; Linux prod unaffected.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEFAULT_ROOT = Path("research_store/checkpoints")


def inventory(root: Path) -> dict[str, int]:
    """Count checkpoints + writes files per thread under the file root.

    Args:
        root: FileCheckpointer root directory.

    Returns:
        dict[str, int]: {"threads": n, "checkpoints": n, "writes_files": n}.
    """
    threads = 0
    checkpoints = 0
    writes_files = 0
    for thread_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        threads += 1
        for path in thread_dir.glob("*.json"):
            if path.name.endswith(".writes.json"):
                writes_files += 1
            else:
                checkpoints += 1
    return {"threads": threads, "checkpoints": checkpoints, "writes_files": writes_files}


async def migrate(root: Path, dsn: str, *, dry_run: bool) -> int:
    """Copy every file checkpoint into the Postgres saver, validating counts.

    Args:
        root: FileCheckpointer root directory.
        dsn: Supabase session-pooler Postgres DSN.
        dry_run: When True, print the plan without touching Postgres.

    Returns:
        int: 0 on success, 2 on missing DSN/extra, 3 on count mismatch.
    """
    import base64

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from knowledge_engine.store.file_checkpointer import FileCheckpointer

    source = FileCheckpointer(root=str(root))
    plan = inventory(root)
    print(f"plan: {plan['threads']} threads, {plan['checkpoints']} checkpoints, {plan['writes_files']} writes files")
    if dry_run:
        print("dry-run: no Postgres writes performed")
        return 0

    migrated = 0
    async with AsyncPostgresSaver.from_conn_string(dsn) as target:
        await target.setup()
        for thread_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for path in sorted(thread_dir.glob("*.json")):
                if path.name.endswith(".writes.json"):
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                checkpoint = source.serde.loads_typed((payload["type"], base64.b64decode(payload["checkpoint"])))
                metadata = source.serde.loads_typed((payload["metadata_type"], base64.b64decode(payload["metadata"])))
                thread_id = thread_dir.name
                config = cast(
                    RunnableConfig,
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": str(payload.get("checkpoint_ns", "")),
                            "checkpoint_id": payload.get("parent_checkpoint_id"),
                        }
                    },
                )
                await target.aput(config, checkpoint, metadata, {})
                migrated += 1
    print(f"migrated {migrated} checkpoints (source files left in place as cold backup)")
    if migrated != plan["checkpoints"]:
        print(f"COUNT MISMATCH: migrated {migrated} != planned {plan['checkpoints']}")
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    Args:
        argv: CLI arguments (defaults to ``sys.argv``).

    Returns:
        int: 0 success, 2 missing DSN, 3 count mismatch.
    """
    parser = argparse.ArgumentParser(description="file-checkpointer -> Supabase Postgres migration")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="FileCheckpointer root directory")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    args = parser.parse_args(argv)

    from knowledge_engine.config import load_dotenv

    load_dotenv()
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn and not args.dry_run:
        print("DATABASE_URL not set; exporting a DSN is required for a real migration")
        return 2
    return asyncio.run(migrate(Path(args.root), dsn, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

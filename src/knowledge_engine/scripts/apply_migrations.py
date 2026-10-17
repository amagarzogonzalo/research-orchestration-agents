"""Apply pending SQL migrations from ``supabase/migrations`` (idempotent).

Tracks applied files in a ``schema_migrations`` table so re-running is a no-op
for anything already applied. Each file runs inside its own transaction: a
failure rolls that file back and stops the run, leaving prior files applied.

Intended as a Render pre-deploy command (or manual via the Render Shell):

    python -m knowledge_engine.scripts.apply_migrations [--dry-run] [--dir DIR]

The connection string comes from ``DATABASE_URL`` (Supabase session pooler).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEFAULT_DIR = Path("supabase/migrations")

_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "  filename text PRIMARY KEY,"
    "  applied_at timestamptz NOT NULL DEFAULT now()"
    ")"
)


def _discover(migrations_dir: Path) -> list[Path]:
    """List ``*.sql`` files in a directory, sorted by filename.

    Args:
        migrations_dir: Directory holding the migration files.

    Returns:
        list[Path]: Sorted migration paths (empty when the directory is absent).
    """
    if not migrations_dir.is_dir():
        return []
    return sorted(migrations_dir.glob("*.sql"))


def _dsn() -> str:
    """Read the database DSN from the environment.

    Returns:
        str: The value of ``DATABASE_URL``.

    Raises:
        SystemExit: When ``DATABASE_URL`` is unset.
    """
    import os

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")
    return dsn


def apply(dsn: str, migrations_dir: Path, *, dry_run: bool = False) -> list[str]:
    """Apply every not-yet-applied migration, in filename order.

    Args:
        dsn: Postgres connection string.
        migrations_dir: Directory holding ``*.sql`` migrations.
        dry_run: When True, report pending files without applying them.

    Returns:
        list[str]: Filenames that were (or would be) applied.

    Raises:
        SystemExit: When the migrations directory does not exist.
    """
    if not migrations_dir.is_dir():
        raise SystemExit(f"migrations directory not found: {migrations_dir}")

    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(_LEDGER_DDL)
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        pending = [path for path in _discover(migrations_dir) if path.name not in applied]
        if dry_run:
            return [path.name for path in pending]

        done: list[str] = []
        for path in pending:
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            done.append(path.name)
        return done


def main(argv: list[str] | None = None) -> int:
    """Run the migration CLI.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        int: Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations.")
    parser.add_argument("--dir", default=str(_DEFAULT_DIR), help="migrations directory")
    parser.add_argument("--dry-run", action="store_true", help="list pending files only")
    args = parser.parse_args(argv)

    to_apply = apply(_dsn(), Path(args.dir), dry_run=args.dry_run)
    if not to_apply:
        print("no pending migrations")
        return 0
    verb = "would apply" if args.dry_run else "applied"
    for name in to_apply:
        print(f"{verb}: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

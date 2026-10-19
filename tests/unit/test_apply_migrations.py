"""Unit tests: migration sentinel baselining (no database connection)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knowledge_engine.scripts.apply_migrations import _SENTINELS, _already_in_place


class FakeCursor:
    """Minimal psycopg cursor double returning a canned row."""

    def __init__(self, row: Any) -> None:
        self._row = row
        self.executed: list[str] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Record the executed SQL.

        Args:
            sql: SQL text.
            params: Query parameters.
        """
        self.executed.append(sql)

    def fetchone(self) -> Any:
        """Return the canned row.

        Returns:
            Any: The row.
        """
        return self._row


class FakeConn:
    """Minimal psycopg connection double."""

    def __init__(self, row: Any) -> None:
        self.cursor_obj = FakeCursor(row)

    def cursor(self) -> FakeCursor:
        """Return the shared cursor.

        Returns:
            FakeCursor: The cursor double.
        """
        return self.cursor_obj


def test_sentinel_hit_means_the_migration_is_already_applied() -> None:
    """A returning sentinel adopts a pre-ledger database without executing SQL."""
    conn = FakeConn((1,))
    assert _already_in_place(conn, "20261012160700_wiki_v2.sql") is True
    assert "information_schema" in conn.cursor_obj.executed[0]


def test_sentinel_miss_means_the_migration_must_run() -> None:
    """A sentinel that returns no row leaves the migration pending."""
    assert _already_in_place(FakeConn(None), "20261012160700_wiki_v2.sql") is False


def test_migrations_without_a_sentinel_are_never_baselined() -> None:
    """Unknown/new migrations execute normally (no sentinel query is run)."""
    conn = FakeConn((1,))
    assert _already_in_place(conn, "20990101000000_new.sql") is False
    assert conn.cursor_obj.executed == []


def test_every_sentinel_targets_an_existing_migration() -> None:
    """Sentinels cannot rot into filenames that no longer exist."""
    names = {path.name for path in Path("supabase/migrations").glob("*.sql")}
    assert set(_SENTINELS) <= names

"""Unit tests: idempotent migration discovery + CLI guards (offline; no DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.scripts import apply_migrations


def test_discover_returns_sorted_sql_only(tmp_path: Path) -> None:
    """Only ``*.sql`` files are discovered, sorted by filename."""
    (tmp_path / "b.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / "a.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert [p.name for p in apply_migrations._discover(tmp_path)] == ["a.sql", "b.sql"]


def test_discover_missing_dir_is_empty(tmp_path: Path) -> None:
    """A missing directory yields an empty list rather than raising."""
    assert apply_migrations._discover(tmp_path / "nope") == []


def test_apply_missing_dir_exits(tmp_path: Path) -> None:
    """Applying against a missing directory fails fast (before any DB import)."""
    with pytest.raises(SystemExit):
        apply_migrations.apply("postgresql://unused", tmp_path / "nope")


def test_main_without_dsn_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The CLI refuses to run without DATABASE_URL configured."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        apply_migrations.main(["--dir", str(tmp_path)])

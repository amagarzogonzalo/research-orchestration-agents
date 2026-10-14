"""Unit tests: retention planner + local store + CLI (before/after)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from knowledge_engine.scripts import retention
from knowledge_engine.scripts.retention import LocalObjectStore, StoredObject

_NOW = time.time()
_OLD = _NOW - 40 * 86400


def _obj(bucket: str, name: str, size: int, ts: float) -> StoredObject:
    return StoredObject(bucket=bucket, name=name, size=size, updated_at=ts)


def test_plan_deletes_old_and_keeps_recent() -> None:
    """Objects older than the cutoff are planned; recent ones are kept."""
    objects = [_obj("articles", "old/article.md", 10, _OLD), _obj("articles", "new/article.md", 10, _NOW)]
    plan = retention.plan_retention(objects, cutoff=_NOW - 30 * 86400)
    assert [o.name for o in plan] == ["old/article.md"]


def test_plan_keeps_newest_runs_even_if_old() -> None:
    """keep_runs protects the N most recent run prefixes regardless of age."""
    objects = [
        _obj("articles", "run-a/article.md", 10, _OLD),
        _obj("articles", "run-b/article.md", 10, _OLD + 1),
        _obj("articles", "run-c/article.md", 10, _OLD + 2),
    ]
    plan = retention.plan_retention(objects, cutoff=_NOW, keep_runs=2)
    assert [o.name for o in plan] == ["run-a/article.md"]


def test_keep_runs_does_not_protect_flat_objects() -> None:
    """Flat objects (bodies) are not run-scoped, so keep_runs never protects them."""
    objects = [_obj("bodies", "abc.md", 10, _OLD), _obj("bodies", "def.md", 10, _OLD)]
    plan = retention.plan_retention(objects, cutoff=_NOW, keep_runs=5)
    assert {o.name for o in plan} == {"abc.md", "def.md"}


def test_summarize_counts_and_bytes() -> None:
    """summarize aggregates counts and bytes per bucket."""
    usage = retention.summarize([_obj("bodies", "a.md", 3, _NOW), _obj("bodies", "b.md", 4, _NOW)])
    assert usage["bodies"] == {"count": 2, "bytes": 7}


def test_local_store_list_and_delete(tmp_path: Path) -> None:
    """LocalObjectStore lists and deletes files under bucket dirs."""
    (tmp_path / "bodies").mkdir()
    (tmp_path / "bodies" / "x.md").write_text("hello", encoding="utf-8")
    store = LocalObjectStore(tmp_path)
    objects = store.list_objects(("bodies",))
    assert [o.name for o in objects] == ["x.md"]
    assert store.delete_objects(objects) == 1
    assert store.list_objects(("bodies",)) == []


def _seed(root: Path) -> None:
    (root / "bodies").mkdir(parents=True)
    old = root / "bodies" / "old.md"
    new = root / "bodies" / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    os.utime(old, (_OLD, _OLD))


def test_cli_dry_run_does_not_delete(tmp_path: Path) -> None:
    """Dry-run reports a plan but leaves files in place."""
    _seed(tmp_path)
    report = tmp_path / "report.json"
    assert retention.main(["--root", str(tmp_path), "--days", "30", "--keep-runs", "0", "--report", str(report)]) == 0
    assert (tmp_path / "bodies" / "old.md").is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["planned"]["bodies"]["count"] == 1


def test_cli_apply_deletes_old(tmp_path: Path) -> None:
    """--apply removes the old object and keeps the recent one (before/after)."""
    _seed(tmp_path)
    assert retention.main(["--root", str(tmp_path), "--days", "30", "--keep-runs", "0", "--apply"]) == 0
    assert not (tmp_path / "bodies" / "old.md").exists()
    assert (tmp_path / "bodies" / "new.md").is_file()


def test_parse_ts_unparseable_keeps_object() -> None:
    """An unparseable timestamp yields 'now' so the object is kept (safe)."""
    assert retention._parse_ts("not-a-date") >= time.time() - 5

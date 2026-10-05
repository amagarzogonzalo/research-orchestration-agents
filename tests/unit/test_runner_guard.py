"""Unit tests: record-into-dirty-dir guard (offline, tmp cassette dirs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals import runner


@pytest.fixture
def cassettes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the runner at an isolated cassette root."""
    root = tmp_path / "cassettes"
    root.mkdir()
    monkeypatch.setattr(runner, "DEFAULT_DIR", root)
    return root


def test_empty_dirs_are_clean(cassettes: Path) -> None:
    """No runs recorded yet: nothing to refuse."""
    cases = [{"id": "research", "samples": 1}]
    assert runner.find_dirty_record_dirs(cases) == {}


def test_missing_dirs_are_clean(cassettes: Path) -> None:
    """Case dirs that do not exist yet are recordable."""
    cases = [{"id": "web", "samples": 2}]
    assert runner.find_dirty_record_dirs(cases) == {}


def test_nonempty_run_dir_is_dirty(cassettes: Path) -> None:
    """Any file in a run dir marks it dirty (stale files diverge replay)."""
    run0 = cassettes / "research" / "run0"
    run0.mkdir(parents=True)
    (run0 / "abc123.json").write_text("{}")
    dirty = runner.find_dirty_record_dirs([{"id": "research", "samples": 1}])
    assert dirty == {"research": ["run0"]}


def test_empty_run_dir_is_clean(cassettes: Path) -> None:
    """Created-but-empty run dirs (fresh wipe) are recordable."""
    (cassettes / "deep" / "run0").mkdir(parents=True)
    assert runner.find_dirty_record_dirs([{"id": "deep", "samples": 1}]) == {}


def test_only_listed_samples_checked(cassettes: Path) -> None:
    """Samples beyond the case count are ignored, not refused."""
    run3 = cassettes / "web" / "run3"
    run3.mkdir(parents=True)
    (run3 / "x.json").write_text("{}")
    assert runner.find_dirty_record_dirs([{"id": "web", "samples": 1}]) == {}

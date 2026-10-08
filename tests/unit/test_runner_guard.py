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


def test_citation_yield_counts_used_footnotes() -> None:
    """Yield = footnoted source ids over retrieved sources (precision signal)."""
    sources = [{"id": "a:1"}, {"id": "b:2"}, {"id": "c:3"}]
    artifact = "Claim one [^a:1] and claim two [^b:2]. Uncited: [^zzz]."
    assert runner._citation_yield(artifact, sources) == "2/3 sources cited"


def test_citation_yield_empty_inputs() -> None:
    """Empty artifacts and source lists yield zero without crashing."""
    assert runner._citation_yield("", []) == "0/0 sources cited"
    assert runner._citation_yield("no footnotes", None) == "0/0 sources cited"


def test_citation_yield_by_tier_splits_scholarly_and_web() -> None:
    """Per-tier yield separates retrieval breadth from generation use."""
    sources = [
        {"id": "openalex:W1", "source": "openalex"},
        {"id": "arxiv:123", "source": "arxiv"},
        {"id": "duckduckgo:https://ex.io/guide", "source": "duckduckgo"},
        {"id": "tavily:https://ex.io/tiers", "source": "tavily"},
    ]
    artifact = "Scholarly claim [^openalex:W1] and web claim [^duckduckgo:https://ex.io/guide]."
    assert runner._citation_yield_by_tier(artifact, sources) == {
        "scholarly": "1/2",
        "web": "1/2",
        "total": "2/4",
    }


def test_citation_yield_by_tier_empty() -> None:
    assert runner._citation_yield_by_tier("", []) == {"scholarly": "0/0", "web": "0/0", "total": "0/0"}
    assert runner._citation_yield_by_tier("no footnotes", None) == {
        "scholarly": "0/0",
        "web": "0/0",
        "total": "0/0",
    }

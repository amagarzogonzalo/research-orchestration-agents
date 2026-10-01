"""Unit tests: worker thread-id derivation (offline, deterministic)."""

from __future__ import annotations

from knowledge_engine.worker import _thread_id


def test_thread_id_is_stable_across_calls() -> None:
    assert _thread_id("same query") == _thread_id("same query")


def test_thread_id_differs_per_query() -> None:
    assert _thread_id("query one") != _thread_id("query two")


def test_thread_id_has_expected_shape() -> None:
    tid = _thread_id("  padded query  ")
    assert tid.startswith("t-")
    assert len(tid) == 14  # "t-" + 12 hex chars
    int(tid[2:], 16)  # must be valid hex
    assert _thread_id("padded query") == tid  # surrounding whitespace ignored

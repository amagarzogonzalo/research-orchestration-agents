"""Unit tests: shared scrape helpers (offline, pure logic)."""

from __future__ import annotations

from knowledge_engine.infrastructure.scrape.common import fallback_title


def test_fallback_title_uses_last_path_segment() -> None:
    assert fallback_title("https://example.org/papers/attention-is-all-you-need") == "attention-is-all-you-need"


def test_fallback_title_strips_trailing_slash() -> None:
    assert fallback_title("https://example.org/papers/") == "papers"


def test_fallback_title_uses_host_when_no_path() -> None:
    assert fallback_title("https://example.org") == "example.org"

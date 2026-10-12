"""Unit tests for the wiki file-store helpers (content addressing, no SQL)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from knowledge_engine.infrastructure.wiki.postgres.files import LocalFileStore, sha256_bytes, sharded_path


def _run(coro: Any) -> Any:
    """Run one awaitable to completion.

    Args:
        coro: An awaitable.

    Returns:
        Any: The awaitable's result.
    """
    return asyncio.run(coro)


def test_sharded_path_matches_hash() -> None:
    """A pointer path is sharded by the first two hex chars."""
    sha = "ab" + "1" * 62
    assert sharded_path(sha) == f"ab/{sha}.md"
    assert sharded_path(sha, suffix=".txt") == f"ab/{sha}.txt"


def test_sharded_path_rejects_bad_hash() -> None:
    """A non-64-hex hash is rejected (the DB CHECK enforces this too)."""
    with pytest.raises(ValueError):
        sharded_path("xyz")


def test_local_store_put_get_and_idempotency(tmp_path) -> None:
    """LocalFileStore writes content-addressed bytes and reads them back."""
    store = LocalFileStore(tmp_path)
    ref = _run(store.put(b"same bytes" * 4))
    again = _run(store.put(b"same bytes" * 4))
    assert ref.sha256 == again.sha256
    assert ref.path == sharded_path(ref.sha256)
    assert _run(store.get(ref)) == b"same bytes" * 4
    assert sha256_bytes(b"same bytes" * 4) == ref.sha256

"""Unit tests: wiki bucket backup against a fake client (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knowledge_engine.scripts.backup_wiki import SupabaseBucketBackup


class _Bucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def list(self, prefix: str | None = None, options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List immediate children of a prefix (folders have ``id=None``).

        Args:
            prefix: Folder prefix.
            options: Ignored.

        Returns:
            list[dict[str, Any]]: Entries with ``name`` and ``id``.
        """
        base = f"{prefix}/" if prefix else ""
        seen: dict[str, dict[str, Any]] = {}
        for key in self._objects:
            if not key.startswith(base):
                continue
            rest = key[len(base) :]
            head = rest.split("/", 1)[0]
            if "/" in rest:
                seen[head] = {"name": head, "id": None}
            else:
                seen[head] = {"name": head, "id": "1"}
        return list(seen.values())

    def download(self, key: str) -> bytes:
        """Return stored bytes.

        Args:
            key: Object key.

        Returns:
            bytes: Content.
        """
        return self._objects[key]


class _Storage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def from_(self, bucket: str) -> _Bucket:
        """Return a bucket handle.

        Args:
            bucket: Bucket name.

        Returns:
            _Bucket: Fake handle.
        """
        return _Bucket(self._objects)


class _Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.storage = _Storage(objects)


def test_backup_downloads_nested_objects(tmp_path: Path) -> None:
    """Nested keys are walked and written preserving their path."""
    objects = {
        "pages/a.md": b"alpha",
        "pages/nested/b.md": b"beta",
        "index.md": b"# index",
    }
    count = SupabaseBucketBackup(_Client(objects)).backup("wiki", tmp_path)
    assert count == 3
    assert (tmp_path / "pages" / "a.md").read_bytes() == b"alpha"
    assert (tmp_path / "pages" / "nested" / "b.md").read_bytes() == b"beta"
    assert (tmp_path / "index.md").read_bytes() == b"# index"


def test_backup_empty_bucket(tmp_path: Path) -> None:
    """An empty bucket writes nothing."""
    assert SupabaseBucketBackup(_Client({})).backup("wiki", tmp_path) == 0

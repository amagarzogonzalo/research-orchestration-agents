"""Unit tests: SupabaseBodyStorage against a fake Supabase client (offline).

Closes the coverage gap: the cloud body store had no test at all (only an
indirect, live-gated one). These pin save/load/refusal and bucket healing.
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.schemas.source import BodyRef, DocumentBody
from knowledge_engine.store.supabase_body_storage import SupabaseBodyStorage


class _Bucket:
    """Fake Storage bucket (upload/download)."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def upload(self, key: str, data: Any, options: dict[str, str] | None = None) -> None:
        """Store bytes under a key.

        Args:
            key: Object key.
            data: Bytes payload.
            options: Upload options (ignored).
        """
        self._objects[key] = bytes(data)

    def download(self, key: str) -> bytes:
        """Return stored bytes or raise.

        Args:
            key: Object key.

        Returns:
            bytes: Stored content.

        Raises:
            FileNotFoundError: When the key is absent.
        """
        if key not in self._objects:
            raise FileNotFoundError(key)
        return self._objects[key]


class _Storage:
    """Fake Storage API (buckets + objects)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.buckets: dict[str, dict[str, bool]] = {}

    def list_buckets(self) -> list[Any]:
        """List buckets with a ``name``/``public`` shape.

        Returns:
            list[Any]: Bucket-like objects.
        """
        return [type("B", (), {"name": name, "public": meta["public"]})() for name, meta in self.buckets.items()]

    def create_bucket(self, name: str, options: dict[str, Any] | None = None) -> None:
        """Create a bucket.

        Args:
            name: Bucket name.
            options: ``{"public": bool}``.
        """
        self.buckets[name] = {"public": bool(options and options.get("public"))}

    def update_bucket(self, name: str, options: dict[str, Any] | None = None) -> None:
        """Update a bucket's public flag.

        Args:
            name: Bucket name.
            options: ``{"public": bool}``.
        """
        self.buckets[name] = {"public": bool(options and options.get("public"))}

    def from_(self, name: str) -> _Bucket:
        """Return a bucket handle.

        Args:
            name: Bucket name.

        Returns:
            _Bucket: Fake bucket.
        """
        return _Bucket(self.objects)


class _Client:
    """Minimal Supabase client exposing ``.storage``."""

    def __init__(self) -> None:
        self.storage = _Storage()


def _body(markdown: str = "# Title\n\nBody text.") -> DocumentBody:
    """Build a deterministic body.

    Args:
        markdown: Body markdown.

    Returns:
        DocumentBody: The body.
    """
    return DocumentBody(url="https://example.org/a", title="T", markdown=markdown, char_count=len(markdown))


async def test_save_uploads_and_returns_cloud_ref() -> None:
    """save() uploads the bytes and returns a bodies/ ref; bucket stays private."""
    client = _Client()
    store = SupabaseBodyStorage(client)
    ref = await store.save(_body())
    assert ref.path.startswith("bodies/") and ref.path.endswith(".md")
    assert ref.url == "https://example.org/a"
    assert client.storage.objects
    assert client.storage.buckets["bodies"]["public"] is False


async def test_load_round_trips_markdown() -> None:
    """A saved body loads back exactly."""
    store = SupabaseBodyStorage(_Client())
    ref = await store.save(_body("# hi\ncloud body"))
    assert await store.load(ref) == "# hi\ncloud body"


async def test_load_rejects_foreign_ref() -> None:
    """A ref that is not a cloud bodies key is refused."""
    store = SupabaseBodyStorage(_Client())
    with pytest.raises(KnowledgeEngineError):
        await store.load(BodyRef(url="u", title="t", path="local/x.md"))


async def test_load_missing_object_raises() -> None:
    """A missing cloud object raises a loud error."""
    store = SupabaseBodyStorage(_Client())
    with pytest.raises(KnowledgeEngineError):
        await store.load(BodyRef(url="u", title="t", path="bodies/deadbeefdeadbeef.md"))


async def test_save_heals_a_public_bucket_to_private() -> None:
    """An existing public bodies bucket is healed to private on save."""
    client = _Client()
    client.storage.buckets["bodies"] = {"public": True}
    store = SupabaseBodyStorage(client)
    await store.save(_body())
    assert client.storage.buckets["bodies"]["public"] is False


def test_key_is_deterministic_and_sixteen_hex() -> None:
    """The Storage key is deterministic and 16 hex chars + ``.md``."""
    store = SupabaseBodyStorage(_Client())
    key = store._key("https://example.org/a")
    assert key == store._key("https://example.org/a")
    assert len(key) == len(".md") + 16

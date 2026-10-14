"""Unit tests: SupabaseArtifactStore against a fake client (offline).

Pins the security posture: buckets are created/healed PRIVATE, and locators are
storage object keys (not public URLs, which a private bucket would not serve).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore


class _BucketHandle:
    """Fake object handle for one bucket."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def upload(self, path: str, data: Any, options: dict[str, str] | None = None) -> None:
        """Store bytes under a path.

        Args:
            path: Object path.
            data: Bytes payload.
            options: Upload options (ignored).
        """
        self._objects[path] = bytes(data)

    def get_public_url(self, path: str) -> str:
        """Return a fake public URL.

        Args:
            path: Object path.

        Returns:
            str: Fake URL.
        """
        return f"https://fake.supabase.co/{path}"

    def download(self, path: str) -> bytes:
        """Return stored bytes or raise.

        Args:
            path: Object path.

        Returns:
            bytes: Stored bytes.

        Raises:
            FileNotFoundError: When the path is absent.
        """
        if path not in self._objects:
            raise FileNotFoundError(path)
        return self._objects[path]


class _Storage:
    """Fake Storage API (buckets + objects)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.buckets: dict[str, dict[str, bool]] = {}

    def list_buckets(self) -> list[Any]:
        """List buckets with ``name``/``public``.

        Returns:
            list[Any]: Bucket-like objects.
        """
        return [SimpleNamespace(name=name, public=meta["public"]) for name, meta in self.buckets.items()]

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

    def from_(self, name: str) -> _BucketHandle:
        """Return a bucket handle.

        Args:
            name: Bucket name.

        Returns:
            _BucketHandle: Fake handle.
        """
        return _BucketHandle(self.objects)


class _Client:
    """Minimal Supabase client exposing ``.storage``."""

    def __init__(self) -> None:
        self.storage = _Storage()


def _artifact(body: str = "hello") -> KnowledgeArtifact:
    return KnowledgeArtifact(id="t1", title="T", body_markdown=body, frontmatter_yaml="title: T\n", generated_at="")


def test_buckets_are_created_private() -> None:
    """Both buckets are created private (least privilege)."""
    client = _Client()
    SupabaseArtifactStore(client)._ensure_buckets()
    assert client.storage.buckets["articles"]["public"] is False
    assert client.storage.buckets["sources"]["public"] is False


def test_public_buckets_are_healed_to_private() -> None:
    """A pre-existing public bucket is healed to private."""
    client = _Client()
    client.storage.buckets["articles"] = {"public": True}
    client.storage.buckets["sources"] = {"public": True}
    SupabaseArtifactStore(client)._ensure_buckets()
    assert client.storage.buckets["articles"]["public"] is False
    assert client.storage.buckets["sources"]["public"] is False


def test_put_article_locator_is_object_key() -> None:
    """The locator is a storage key (not a public URL)."""
    client = _Client()
    locator = SupabaseArtifactStore(client).put_article("t1", _artifact("body"))
    assert locator == "articles/t1/article.md"
    assert client.storage.objects["t1/article.md"] == b"---\ntitle: T\n---\n\nbody\n"


def test_put_sources_locator_is_object_key() -> None:
    """The sources locator is a storage key."""
    client = _Client()
    store = SupabaseArtifactStore(client)
    locator = store.put_sources("t1", [SourceRecord(id="openalex:W1", title="One", source=SearchSource.OPENALEX)])
    assert locator == "sources/t1/sources.json"
    assert store.get_sources("t1")[0].id == "openalex:W1"


def test_article_round_trips() -> None:
    """get_article returns the stored bytes."""
    store = SupabaseArtifactStore(_Client())
    store.put_article("t1", _artifact("round trip"))
    assert "round trip" in (store.get_article("t1") or "")

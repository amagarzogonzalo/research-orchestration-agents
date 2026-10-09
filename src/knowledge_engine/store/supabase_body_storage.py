"""SupabaseBodyStorage — cloud-backed BodyStoragePort (ADR-0013 extension).

When workflow STATE is cloud-durable (postgres checkpointer), body content
must be too: a thread may resume on another machine, where local ``.md``
files are gone. Bodies land in the ``bodies`` Storage bucket as
``<sha256(url)[:16]>.md`` (same deterministic naming as LocalBodyStorage);
``BodyRef.path`` carries the Storage object key (``bodies/<name>.md``).
"""

from __future__ import annotations

import hashlib
from typing import Any

from knowledge_engine.core.exceptions import ConfigurationError, KnowledgeEngineError
from knowledge_engine.core.ports import BodyStoragePort
from knowledge_engine.schemas.source import BodyRef, DocumentBody

_BUCKET = "bodies"


class SupabaseBodyStorage(BodyStoragePort):
    """Supabase Storage-backed body persistence (cross-machine safe).

    Args:
        client: Supabase client (from :meth:`from_env`).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> SupabaseBodyStorage:
        """Build from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

        Returns:
            SupabaseBodyStorage: Configured storage.

        Raises:
            ConfigurationError: When keys or the extra are missing.
        """
        import os

        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SECRET_KEY", "")
        if not url or not key:
            raise ConfigurationError("body_store_kind='supabase' needs SUPABASE_URL and SUPABASE_SECRET_KEY")
        try:
            from supabase import create_client
        except ImportError as exc:
            raise ConfigurationError("supabase bodies need the 'supabase' extra: uv sync --extra supabase") from exc
        return cls(create_client(url, key))

    def _key(self, url: str) -> str:
        """Deterministic Storage key for a URL.

        Args:
            url: Body source URL.

        Returns:
            str: ``<name>.md`` Storage key.
        """
        return f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.md"

    def _ensure_bucket(self) -> None:
        """Create the bodies bucket as PUBLIC; heal a pre-existing private one.

        Idempotent: steady state is one list call, zero writes.
        """
        existing = {b.name: b.public for b in self._client.storage.list_buckets()}
        if _BUCKET not in existing:
            try:
                self._client.storage.create_bucket(_BUCKET, options={"public": True})
            except Exception:  # noqa: BLE001 - duplicate-bucket races are benign
                pass
        elif not existing[_BUCKET]:
            try:
                self._client.storage.update_bucket(_BUCKET, {"public": True})
            except Exception:  # noqa: BLE001 - heal attempt is best-effort
                pass

    async def save(self, body: DocumentBody) -> BodyRef:
        """Upsert body bytes; return the ref (Cloud key in ``path``).

        Args:
            body: Scraped body to persist.

        Returns:
            BodyRef: Deterministic reference.
        """
        self._ensure_bucket()
        key = self._key(body.url)
        self._client.storage.from_(_BUCKET).upload(
            key,
            body.markdown.encode("utf-8"),  # supabase-py: bytes = content
            # text/plain renders inline in browsers (text/markdown = download)
            {"upsert": "true", "content-type": "text/plain; charset=utf-8"},
        )
        return BodyRef(
            url=body.url,
            title=body.title,
            path=f"{_BUCKET}/{key}",
            char_count=len(body.markdown),
            crawl_method=body.crawl_method,
            captured_at=body.captured_at,
        )

    async def load(self, ref: BodyRef) -> str:
        """Download body bytes by ref key (traversal-safe: keyderived, not user input).

        Args:
            ref: Reference returned by :meth:`save`.

        Returns:
            str: Markdown content.

        Raises:
            KnowledgeEngineError: When the object is missing or the ref is foreign.
        """
        if not ref.path.startswith(f"{_BUCKET}/"):
            raise KnowledgeEngineError(f"body ref is not a cloud bodies key: {ref.path}")
        full_key = ref.path.removeprefix(f"{_BUCKET}/")
        try:
            data = self._client.storage.from_(_BUCKET).download(full_key)
        except Exception as exc:  # noqa: BLE001 - missing object reads as a loud miss
            raise KnowledgeEngineError(f"cloud body ref missing: {ref.path}") from exc
        raw = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        return raw


__all__ = ["SupabaseBodyStorage"]

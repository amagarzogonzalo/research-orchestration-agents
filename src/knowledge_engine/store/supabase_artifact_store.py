"""SupabaseArtifactStore — cloud persistence for published artifacts (ADR-0012).

Stores ``article.md`` under the ``articles`` bucket and ``sources.json`` under
``sources``, keyed by ``<thread_id>/<filename>``; upsert semantics (a re-run
replaces its thread's objects); readers return the object bytes. Locators are
public Storage URLs. Requires the ``supabase`` extra (opt-in per AGENTS rule 4)
and ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.
"""

from __future__ import annotations

import json
from typing import Any

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.core.ports import ArtifactStorePort
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SourceRecord

_ARTICLES_BUCKET = "articles"
_SOURCES_BUCKET = "sources"


class SupabaseArtifactStore(ArtifactStorePort):
    """Supabase Storage-backed published-artifact store (survives ephemeral hosts).

    Args:
        client: Supabase client (from :meth:`from_env`).
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._mime_types: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> SupabaseArtifactStore:
        """Build the store from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

        Returns:
            SupabaseArtifactStore: Configured store.

        Raises:
            ConfigurationError: When the keys or the ``supabase`` extra are missing.
        """
        import os

        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SECRET_KEY", "")
        if not url or not key:
            raise ConfigurationError("artifact_store_kind='supabase' needs SUPABASE_URL and SUPABASE_SECRET_KEY")
        try:
            from supabase import create_client
        except ImportError as exc:
            raise ConfigurationError(
                "the supabase adapter needs the 'supabase' extra: uv sync --extra supabase"
            ) from exc
        return cls(create_client(url, key))

    def _ensure_buckets(self) -> None:
        """Create missing buckets as PUBLIC; heal pre-existing private ones.

        Published artifacts must be browser-viewable at the public URL —
        the locator is the read surface (no download tooling needed).
        Idempotent: normal steady state is one list call, zero writes.
        """
        existing = {b.name: b.public for b in self._client.storage.list_buckets()}
        for bucket, mime in (
            # text/plain (not text/markdown): browsers RENDER text/plain inline;
            # text/markdown makes Chrome download the file instead of showing it.
            (_ARTICLES_BUCKET, "text/plain; charset=utf-8"),
            (_SOURCES_BUCKET, "application/json"),
        ):
            if bucket not in existing:
                try:
                    self._client.storage.create_bucket(bucket, options={"public": True})
                except Exception:  # noqa: BLE001 - duplicate-bucket races are benign
                    pass
            elif not existing[bucket]:
                try:
                    self._client.storage.update_bucket(bucket, {"public": True})
                except Exception:  # noqa: BLE001 - heal attempt is best-effort
                    pass
            self._mime_types[bucket] = mime

    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        """Upsert the article to Storage; return its public URL.

        Args:
            thread_id: Run thread identifier.
            artifact: Final artifact.

        Returns:
            str: Public URL of the stored article.
        """
        self._ensure_buckets()
        text = f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}\n"
        path = f"{thread_id}/article.md"
        self._client.storage.from_(_ARTICLES_BUCKET).upload(
            path,
            text.encode("utf-8"),  # supabase-py: bytes = content, str = file path
            {"upsert": "true", "content-type": self._mime_types[_ARTICLES_BUCKET]},
        )
        return str(self._client.storage.from_(_ARTICLES_BUCKET).get_public_url(path))

    def put_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        """Upsert sources.json to Storage; return its public URL.

        Args:
            thread_id: Run thread identifier.
            sources: Resolved source records.

        Returns:
            str: Public URL of the stored sources.
        """
        self._ensure_buckets()
        payload = json.dumps([s.model_dump(mode="json") for s in sources], indent=2, ensure_ascii=False)
        path = f"{thread_id}/sources.json"
        self._client.storage.from_(_SOURCES_BUCKET).upload(
            path,
            payload.encode("utf-8"),  # supabase-py: bytes = content, str = file path
            {"upsert": "true", "content-type": self._mime_types[_SOURCES_BUCKET]},
        )
        return str(self._client.storage.from_(_SOURCES_BUCKET).get_public_url(path))

    def get_article(self, thread_id: str) -> str | None:
        """Download the article bytes; None when the object is absent.

        Args:
            thread_id: Run thread identifier.

        Returns:
            str | None: Article text or None.
        """
        try:
            data = self._client.storage.from_(_ARTICLES_BUCKET).download(f"{thread_id}/article.md")
        except Exception:  # noqa: BLE001 - missing object reads as None (potentially a 404)
            return None
        if isinstance(data, bytes):
            return data.decode("utf-8")
        return str(data)

    def get_sources(self, thread_id: str) -> list[SourceRecord]:
        """Download persisted sources (empty when absent).

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[SourceRecord]: Persisted sources.
        """
        try:
            data = self._client.storage.from_(_SOURCES_BUCKET).download(f"{thread_id}/sources.json")
        except Exception:  # noqa: BLE001 - missing object reads as empty
            return []
        raw = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        return [SourceRecord.model_validate(item) for item in json.loads(raw)]


__all__ = ["SupabaseArtifactStore"]

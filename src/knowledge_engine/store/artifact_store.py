"""Artifact stores — interchangeable persistence for published artifacts (ADR-0012).

* :class:`LocalArtifactStore` — byte-identical to the legacy ``ResearchStore``
  layout (``<root>/<thread>/article.md`` + ``sources.json``); the portable
  default.
* :class:`SupabaseArtifactStore` — lives in :mod:`knowledge_engine.store.supabase_artifact_store`
  (kept separate so the ``supabase`` extra stays opt-in and unimported here).
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_engine.core.ports import ArtifactStorePort
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SourceRecord


class LocalArtifactStore(ArtifactStorePort):
    """File-backed published-artifact store; the portable default.

    Args:
        root: Store root directory (created on demand).
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _article_path(self, thread_id: str) -> Path:
        """Article path for a thread.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: article.md path.
        """
        return self.root / thread_id / "article.md"

    def _sources_path(self, thread_id: str) -> Path:
        """Sources path for a thread.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: sources.json path.
        """
        return self.root / thread_id / "sources.json"

    def put_article(self, thread_id: str, artifact: KnowledgeArtifact) -> str:
        """Write the artifact (frontmatter + body) to article.md.

        Args:
            thread_id: Run thread identifier.
            artifact: Final artifact.

        Returns:
            str: Locator (relative path string).
        """
        path = self._article_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}\n", encoding="utf-8")
        return str(path)

    def put_sources(self, thread_id: str, sources: list[SourceRecord]) -> str:
        """Serialize resolved sources to sources.json.

        Args:
            thread_id: Run thread identifier.
            sources: Resolved source records.

        Returns:
            str: Locator (relative path string).
        """
        path = self._sources_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [s.model_dump(mode="json") for s in sources]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def get_article(self, thread_id: str) -> str | None:
        """Load the article markdown, or None when absent.

        Args:
            thread_id: Run thread identifier.

        Returns:
            str | None: Article text or None.
        """
        path = self._article_path(thread_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_sources(self, thread_id: str) -> list[SourceRecord]:
        """Load persisted sources (empty when absent).

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[SourceRecord]: Persisted sources.
        """
        path = self._sources_path(thread_id)
        if not path.exists():
            return []
        return [SourceRecord.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


__all__ = ["LocalArtifactStore"]

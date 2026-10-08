"""ResearchStore — the files-only persistence layer.

Everything an experiment produces lives under a local folder, never in a
server database. This keeps the library portable: copy the ``research_store/``
directory (or a single run folder) into any machine/project and the data moves
with it.

Layout per run::

    research_store/<thread_id>/
        metadata.json     # run context: query, workflow, status, models used
        events.jsonl      # append-only trace log (one JSON per line)
        sources.json      # resolved SourceRecords
        article.md        # final artifact (body + frontmatter)

The :class:`FileCheckpointer` (``checkpoints`` subdir) provides the LangGraph
durability/HITL checkpoint store as plain JSON files.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knowledge_engine.core.ports import ArtifactStorePort
from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SourceRecord

logger = logging.getLogger(__name__)


class ResearchStore:
    """Create, append to, and read run folders under a root directory.

    Published-artifact persistence routes through an injected
    :class:`ArtifactStorePort` (ADR-0012); ``None`` builds the portable
    :class:`~knowledge_engine.store.artifact_store.LocalArtifactStore`,
    byte-identical to the legacy behavior so evals stay green.

    Args:
        root: Store root directory.
        artifacts: Optional artifact store override (composition-root only).
    """

    def __init__(self, root: str | Path, artifacts: ArtifactStorePort | None = None) -> None:
        self.root = Path(root)
        if artifacts is None:
            from knowledge_engine.store.artifact_store import LocalArtifactStore

            artifacts = LocalArtifactStore(self.root)
        self.artifacts: ArtifactStorePort = artifacts

    # -- paths ----------------------------------------------------------------
    def run_dir(self, thread_id: str) -> Path:
        """Return the directory for a run, creating parents if needed.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        path = self.root / thread_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve(self, thread_id: str, filename: str) -> Path:
        """Resolve a file path.

        Args:
            thread_id: Run thread identifier.
            filename: File name within the run directory.

        Returns:
            Path: Resolved path.

        """
        return self.run_dir(thread_id) / filename

    # -- writes ---------------------------------------------------------------
    def metadata_path(self, thread_id: str) -> Path:
        """Return the metadata.json path for a run.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        return self._resolve(thread_id, "metadata.json")

    def events_path(self, thread_id: str) -> Path:
        """Return the events.jsonl path for a run.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        return self._resolve(thread_id, "events.jsonl")

    def sources_path(self, thread_id: str) -> Path:
        """Return the sources.json path for a run.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        return self._resolve(thread_id, "sources.json")

    def article_path(self, thread_id: str) -> Path:
        """Return the article.md path for a run.

        Args:
            thread_id: Run thread identifier.

        Returns:
            Path: Resolved path.

        """
        return self._resolve(thread_id, "article.md")

    def write_metadata(self, thread_id: str, metadata: dict[str, Any]) -> None:
        """Overwrite a run's metadata.json with the given dict.

        Args:
            thread_id: Run thread identifier.
            metadata: Run metadata mapping.
        """
        self._resolve(thread_id, "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def append_event(self, thread_id: str, event: dict[str, Any]) -> None:
        """Append one trace event to a run's events.jsonl (atomic-ish append).

        Args:
            thread_id: Run thread identifier.
            event: Trace event mapping.
        """
        event = {**event, "ts": datetime.now(UTC).isoformat(timespec="seconds")}
        with self.events_path(thread_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_sources(self, thread_id: str, sources: Iterable[SourceRecord]) -> None:
        """Serialize resolved sources to sources.json.

        Args:
            thread_id: Run thread identifier.
            sources: Resolved source records.
        """
        records = list(sources)
        self.artifacts.put_sources(thread_id, records)
        payload = [s.model_dump(mode="json") for s in records]
        self.sources_path(thread_id).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def write_article(self, artifact: KnowledgeArtifact, thread_id: str) -> None:
        """Persist the final artifact as a Markdown file with YAML frontmatter.

        Args:
            artifact: Final knowledge artifact.
            thread_id: Run thread identifier.
        """
        text = f"---\n{artifact.frontmatter_yaml}---\n\n{artifact.body_markdown}\n"
        self.artifacts.put_article(thread_id, artifact)
        self.article_path(thread_id).write_text(text, encoding="utf-8")
        logger.info("wrote article for thread=%s -> %s", thread_id, _rel(self.article_path(thread_id)))

    # -- reads ----------------------------------------------------------------
    def read_metadata(self, thread_id: str) -> dict[str, Any]:
        """Load a run's metadata.json, defaulting to an empty dict if absent.

        Args:
            thread_id: Run thread identifier.

        Returns:
            dict[str, Any]: Result mapping.

        """
        path = self.metadata_path(thread_id)
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data)

    def read_events(self, thread_id: str) -> list[dict[str, Any]]:
        """Return the run's event log (empty list if absent).

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[dict[str, Any]]: Result rows.

        """
        path = self.events_path(thread_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def read_sources(self, thread_id: str) -> list[SourceRecord]:
        """Deserialize sources.json into SourceRecord objects.

        Args:
            thread_id: Run thread identifier.

        Returns:
            list[SourceRecord]: Resolved sources.

        """
        path = self.sources_path(thread_id)
        if not path.exists():
            return []
        return [SourceRecord.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]

    def exists(self, thread_id: str) -> bool:
        """Return True if the run folder has any persisted content.

        Args:
            thread_id: Run thread identifier.

        Returns:
            bool: True on success.

        """
        return self.run_dir(thread_id).exists() and any(self.run_dir(thread_id).iterdir())


def _rel(path: Path) -> str:
    """Return a display-relative path.

    Args:
        path: File path.

    Returns:
        str: Result text.

    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


__all__ = ["ResearchStore", "KnowledgeArtifact", "SourceRecord"]

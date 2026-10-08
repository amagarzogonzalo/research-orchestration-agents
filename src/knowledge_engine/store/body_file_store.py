"""LocalBodyStorage — filesystem adapter for :class:`BodyStoragePort` (ADR 0010).

Bodies land as UTF-8 Markdown files under ``<root>/<sha256(url)[:16]>.md``:
deterministic (same URL -> same path, idempotent re-fetch), human-inspectable,
and portable (the folder moves with ``research_store/``). ``load`` refuses any
reference resolving outside the root — the ref strings round-trip through
checkpoints, so containment is enforced even against tampered state.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.core.ports import BodyStoragePort
from knowledge_engine.schemas.source import BodyRef, DocumentBody

_READ_CHUNK_WARN_CHARS = 0


class LocalBodyStorage(BodyStoragePort):
    """File-backed body storage; references are absolute paths inside ``root``.

    Args:
        root: Directory holding body files (created on demand).
    """

    def __init__(self, root: str | Path = "research_store/bodies") -> None:
        self.root = Path(root)

    async def save(self, body: DocumentBody) -> BodyRef:
        """Write the markdown under a deterministic name; return the ref.

        Args:
            body: Scraped body to persist.

        Returns:
            BodyRef: Pointer whose ``path`` is inside the storage root.
        """
        digest = hashlib.sha256(body.url.encode("utf-8")).hexdigest()[:16]
        path = self.root / f"{digest}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.markdown, encoding="utf-8")
        return BodyRef(
            url=body.url,
            title=body.title,
            path=str(path.resolve()),
            char_count=len(body.markdown),
            crawl_method=body.crawl_method,
            captured_at=body.captured_at,
        )

    async def load(self, ref: BodyRef) -> str:
        """Read the markdown a ref points at, refusing anything outside ``root``.

        Args:
            ref: Reference returned by :meth:`save`.

        Returns:
            str: Markdown content.

        Raises:
            KnowledgeEngineError: When the ref escapes the root or is missing.
        """
        resolved = Path(ref.path).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise KnowledgeEngineError(f"body ref escapes storage root: {ref.path}")
        if not resolved.is_file():
            raise KnowledgeEngineError(f"body ref is missing: {ref.path}")
        return resolved.read_text(encoding="utf-8")


__all__ = ["LocalBodyStorage"]

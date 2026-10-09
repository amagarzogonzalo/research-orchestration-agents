"""Shared in-memory fakes for wiki contract tests (no disk, no network)."""

from __future__ import annotations

import hashlib
from typing import Any

from knowledge_engine.schemas.wiki import IngestResult, WikiHit, WikiVerdict


class FakeWikiStore:
    """In-memory WikiQueryPort + WikiIngestPort; a spy for call assertions.

    Attributes:
        calls_search: Number of search_wiki calls.
        calls_read: Number of read_page calls.
        calls_ingest: Number of ingest calls.
        pages: slug -> page text (mutable test fixture).
        filed_query_sha: slug -> sha256 of the filing query (exact-hit keys).
    """

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.calls_search = 0
        self.calls_read = 0
        self.calls_ingest = 0
        self.calls_compile = 0
        self.calls_thesis = 0
        self.pages: dict[str, str] = dict(pages or {})
        self.filed_query_sha: dict[str, str] = {}
        self.last_compile: dict[str, Any] | None = None
        self.last_thesis: dict[str, Any] | None = None
        self.feedback_store: dict[str, list[str]] = {}
        self._cache: dict[str, str] = {}

    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Search pages by naive term overlap.

        Args:
            query: Query string.
            limit: Max hits.

        Returns:
            list[WikiHit]: Ranked hits (query_sha256 included when filed).
        """
        self.calls_search += 1
        terms = [t for t in query.lower().split() if t]
        hits: list[WikiHit] = []
        for slug, text in sorted(self.pages.items()):
            value = sum(1 for t in terms if t in text.lower())
            if value:
                hits.append(
                    WikiHit(
                        slug=slug,
                        title=slug,
                        path=f"fake://{slug}",
                        score=float(value),
                        query_sha256=self.filed_query_sha.get(slug, ""),
                    )
                )
        hits.sort(key=lambda h: (-h.score, h.slug))
        return hits[:limit]

    async def read_page(self, slug: str) -> str | None:
        """Read one page.

        Args:
            slug: Page slug.

        Returns:
            str | None: Page text or None.
        """
        self.calls_read += 1
        return self.pages.get(slug)

    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """Ingest by hashing the article text into a fake page.

        Args:
            thread_id: Thread id.
            query: Query string.
            article_path: Article path (contents read by the caller fixture).

        Returns:
            IngestResult: Verdict new/update/no_material.
        """
        self.calls_ingest += 1
        digest = hashlib.sha256(article_path.encode("utf-8")).hexdigest()
        slug = query.lower().replace(" ", "-")[:40] or "research-notes"
        prior = self._cache.get(thread_id)
        if prior == digest:
            return IngestResult(verdict=WikiVerdict.NO_MATERIAL, page_path=f"fake://{slug}", content_sha256=digest)
        verdict = WikiVerdict.UPDATE if slug in self.pages else WikiVerdict.NEW
        self.pages[slug] = f"---\ntype: source\n---\n\n{slug}"
        self.filed_query_sha[slug] = hashlib.sha256(query.encode("utf-8")).hexdigest()
        self._cache[thread_id] = digest
        return IngestResult(verdict=verdict, page_path=f"fake://{slug}", pages_touched=1, content_sha256=digest)

    async def compile_extraction(self, thread_id: str, query: str, extraction: Any, feedback: list[str]) -> list[str]:
        """File fake draft locators for each extracted name.

        Args:
            thread_id: Thread id (recorded for assertions).
            query: Query string.
            extraction: WikiExtraction payload.
            feedback: Feedback lines.

        Returns:
            list[str]: Fake draft locators.
        """
        self.calls_compile += 1
        self.last_compile = {
            "thread_id": thread_id,
            "query": query,
            "extraction": extraction,
            "feedback": list(feedback),
        }
        names = list(extraction.concepts) + list(extraction.entities)
        return [f"fake://drafts/{n.lower().replace(' ', '-')}" for n in names]

    async def file_thesis(self, thread_id: str, thesis: Any) -> str:
        """Record the thesis payload and return a fake locator.

        Args:
            thread_id: Thread id.
            thesis: WikiThesis payload.

        Returns:
            str: Fake locator.
        """
        self.calls_thesis += 1
        self.last_thesis = {"thread_id": thread_id, "thesis": thesis}
        return f"fake://synthesis/{thesis.claim.lower().replace(' ', '-')[:40]}"

    async def feedback_for(self, query: str) -> list[str]:
        """Return recorded feedback lines.

        Args:
            query: Query string.

        Returns:
            list[str]: Feedback lines.
        """
        return self.feedback_store.get(query, [])


def fake_supabase_client(pages: dict[str, str]) -> Any:
    """Build a minimal fake Supabase client for wiki bucket operations.

    Args:
        pages: Initial bucket contents keyed by object path (e.g. ``pages/x.md``).

    Returns:
        Any: Object with ``storage.list_buckets/from_/list/download/upload``.
    """
    store = dict(pages)

    class _Bucket:
        def list(self, prefix: str | None = None) -> list[dict[str, str]]:
            names = sorted(k[len(prefix) + 1 :] for k in store if prefix and k.startswith(prefix + "/"))
            return [{"name": n} for n in names]

        def download(self, key: str) -> bytes:
            if key not in store:
                raise FileNotFoundError(key)
            return store[key].encode("utf-8")

        def upload(self, key: str, data: bytes, options: dict[str, str] | None = None) -> None:
            store[key] = data.decode("utf-8")

    class _Storage:
        def list_buckets(self) -> list[Any]:
            return [type("B", (), {"name": "wiki", "public": False})()]

        def from_(self, bucket: str) -> _Bucket:
            return _Bucket()

    class _Client:
        storage = _Storage()

    return _Client()


__all__ = ["FakeWikiStore", "fake_supabase_client"]

"""Shared in-memory fakes for wiki contract tests (no disk, no network)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from knowledge_engine.infrastructure.wiki import common
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
        self.calls_answer = 0
        self.pages: dict[str, str] = dict(pages or {})
        self.filed_query_sha: dict[str, str] = {}
        self.last_compile: dict[str, Any] | None = None
        self.last_thesis: dict[str, Any] | None = None
        self.last_answer: dict[str, Any] | None = None
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
        for other_thread, recorded in self._cache.items():
            if recorded == digest and other_thread != thread_id:
                return IngestResult(
                    verdict=WikiVerdict.NO_MATERIAL,
                    page_path=f"fake://{slug}",
                    content_sha256=digest,
                )
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

    async def file_answer(self, thread_id: str, query: str, article_path: str) -> str:
        """Record an answer filing and return a fake locator.

        Args:
            thread_id: Thread id.
            query: Query string.
            article_path: Article path.

        Returns:
            str: Fake locator.
        """
        self.calls_answer += 1
        self.last_answer = {"thread_id": thread_id, "query": query, "article_path": article_path}
        return f"fake://queries/{query.lower().replace(' ', '-')[:40]}"

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

    client = _Client()
    client._store = store  # test visibility into the fake bucket
    return client


class FakePgConnection:
    """In-memory stand-in for a psycopg ``Connection`` over the wiki schema.

    Emulates just the SQL the :class:`PostgresWikiStore` emits so the contract
    suite runs offline. ``reads`` counts result-row fetches — the Loop 2 metric
    that proves search returns one result set instead of N page downloads.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {"wiki_pages": [], "wiki_links": [], "wiki_log": []}
        self.autocommit = True
        self.reads = 0

    def __enter__(self) -> FakePgConnection:
        """Enter the context manager (yield self, mirroring a psycopg conn)."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the context manager (no-op; state persists in ``tables``)."""
        return None

    def cursor(self) -> FakePgCursor:  # type: ignore[override]
        """Return a new cursor bound to this connection.

        Returns:
            FakePgCursor: Cursor.
        """
        return FakePgCursor(self)

    def commit(self) -> None:
        """No-op (DML is applied eagerly)."""

    def close(self) -> None:
        """No-op."""


class FakePgCursor:
    """A cursor over :class:`FakePgConnection` handling the wiki SQL subset.

    The dispatcher matches the store's statement shapes by distinctive prefixes
    and applies them to in-memory row lists, mimicking Postgres-upsert + FTS
    ranking ordering so contract tests observe the same adapter behavior.
    """

    def __init__(self, conn: FakePgConnection) -> None:
        self.conn = conn
        self.rowcount = 0
        self._rows: list[tuple[Any, ...]] = []
        self._offset = 0

    def __enter__(self) -> FakePgCursor:
        """Enter the context manager (return self)."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the context manager (no-op on real results)."""
        self._rows = []
        self._offset = 0

    def _pages(self) -> list[dict[str, Any]]:
        """Alias to the wiki_pages table.

        Returns:
            list[dict[str, Any]]: Page rows.
        """
        return self.conn.tables["wiki_pages"]

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> FakePgCursor:
        """Dispatch a statement to the in-memory model (returns ``self``).

        Args:
            sql: SQL text.
            params: Positional parameters.

        Returns:
            FakePgCursor: Self for chaining.
        """
        args: tuple[Any, ...] = tuple(params or ())
        stmt = sql.strip()

        if "CREATE TABLE IF NOT EXISTS wiki_pages" in stmt:
            self.conn.tables.setdefault("wiki_pages", [])
        elif "CREATE TABLE IF NOT EXISTS wiki_links" in stmt:
            self.conn.tables.setdefault("wiki_links", [])
        elif "CREATE TABLE IF NOT EXISTS wiki_log" in stmt:
            self.conn.tables.setdefault("wiki_log", [])
        elif stmt.startswith("CREATE INDEX") or stmt.startswith("ALTER TABLE") or stmt.startswith("CREATE EXTENSION"):
            self.rowcount = 0
        elif stmt.startswith("INSERT INTO wiki_pages"):
            self._insert_pages(args)
        elif stmt.startswith("INSERT INTO wiki_links"):
            self._insert_link(args)
        elif stmt.startswith("INSERT INTO wiki_log"):
            self.conn.tables["wiki_log"].append({"op": args[0], "title": args[1]})
            self.rowcount = 1
        elif stmt.startswith("SELECT slug FROM wiki_pages WHERE thread"):
            thread_id, digest = args
            self._rows = [
                (r["slug"],) for r in self._pages() if r["thread"] == thread_id and r["content_sha256"] == digest
            ]
            self.rowcount = len(self._rows)
        elif "SELECT thread, slug FROM wiki_pages WHERE content_sha256" in stmt:
            digest, thread_id = args
            self._rows = [
                (r["thread"], r["slug"])
                for r in self._pages()
                if r["content_sha256"] == digest and r["thread"] != thread_id
            ]
            self.rowcount = len(self._rows)
        elif stmt.startswith("SELECT 1 FROM wiki_pages WHERE slug"):
            (slug,) = args
            self._rows = [(1,)] if any(r["slug"] == slug for r in self._pages()) else []
            self.rowcount = len(self._rows)
        elif stmt.startswith("SELECT body_md FROM wiki_pages WHERE slug"):
            (slug,) = args
            self._rows = [(r["body_md"],) for r in self._pages() if r["slug"] == slug]
            self.rowcount = len(self._rows)
        elif stmt.startswith("SELECT slug, body_md FROM wiki_pages"):
            self._rows = [(r["slug"], r["body_md"]) for r in self._pages() if r["status"] == "active"]
            self.rowcount = len(self._rows)
        elif stmt.startswith("SELECT DISTINCT dst FROM wiki_links"):
            self._rows = [(r["dst"],) for r in self.conn.tables["wiki_links"]]
            self.rowcount = len(self._rows)
        elif stmt.startswith("SELECT slug, title, body_md"):
            self._fts_search(args)
        elif "SELECT title FROM wiki_log WHERE op" in stmt:
            (slug,) = args
            self._rows = [
                (r["title"],) for r in self.conn.tables["wiki_log"] if r["op"] == "feedback" and r["title"] == slug
            ]
            self.rowcount = len(self._rows)
        else:
            self._rows = []
            self.rowcount = 0
        self._offset = 0
        return self

    def _insert_pages(self, args: tuple[Any, ...]) -> None:
        """Upsert one page row.

        Args:
            args: (slug, page_type, title, body_md, frontmatter_json, thread,
                confidence, status, content_sha256).
        """
        slug, page_type, title, body_md, frontmatter_json, thread, confidence, status, content_sha256 = args
        frontmatter = json.loads(frontmatter_json) if isinstance(frontmatter_json, str) else dict(frontmatter_json)
        row = {
            "slug": slug,
            "page_type": page_type,
            "title": title,
            "body_md": body_md,
            "frontmatter": frontmatter,
            "thread": thread,
            "confidence": confidence,
            "status": status,
            "content_sha256": content_sha256,
        }
        for existing in self._pages():
            if existing["slug"] == slug:
                existing.update(row)
                self.rowcount = 2
                return
        self._pages().append(row)
        self.rowcount = 1

    def _insert_link(self, args: tuple[Any, ...]) -> None:
        """Insert a wiki_links row if absent.

        Args:
            args: (src, dst).
        """
        src, dst = args
        if not any(r["src"] == src and r["dst"] == dst for r in self.conn.tables["wiki_links"]):
            self.conn.tables["wiki_links"].append({"src": src, "dst": dst})
            self.rowcount = 1
        else:
            self.rowcount = 0

    def _fts_search(self, args: tuple[Any, ...]) -> None:
        """Emulate the FTS ranking query: filter, score, rank, limit.

        Args:
            args: (query, query again, limit).
        """
        query, _dup, limit = args
        terms = common.terms(str(query))
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self._pages():
            if row["status"] != "active":
                continue
            fm = row["frontmatter"] or {}
            value = common.score(terms, row["title"], row["body_md"]) if terms else 0.0
            if value <= 0.0:
                continue
            scored.append((value, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["slug"]))
        rows: list[tuple[Any, ...]] = []
        for score, row in scored[: int(limit)]:
            fm = row["frontmatter"] or {}
            rows.append(
                (
                    row["slug"],
                    row["title"],
                    row["body_md"],
                    str(fm.get("tldr") or ""),
                    str(fm.get("query_sha256") or ""),
                    score,
                )
            )
        self._rows = rows
        self.rowcount = len(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        """Return the next row, or None at the end (counts a read).

        Returns:
            tuple[Any, ...] | None: Next row or None.
        """
        self.conn.reads += 1
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        """Return all rows and reset (counts one read).

        Returns:
            list[tuple[Any, ...]]: Remaining rows.
        """
        self.conn.reads += 1
        rows = self._rows[self._offset :]
        self._rows = []
        self._offset = 0
        return rows


_WIKILINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")


def fake_pg_connection() -> FakePgConnection:
    """Build a fake psycopg connection for the Postgres wiki adapter tests.

    Returns:
        FakePgConnection: In-memory connection.
    """
    return FakePgConnection()


__all__ = ["FakePgConnection", "FakeWikiStore", "fake_pg_connection", "fake_supabase_client"]

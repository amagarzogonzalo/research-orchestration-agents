"""PostgresWikiStore facade: async, transactional, ports unchanged (SRP).

Composition root over the wiki v2 schema. Owns orchestration — one transaction
per operation, verdicts, de-dup (cross-thread lookup + UNIQUE(page_id,
content_sha256) mapping to NO_MATERIAL), draft-vs-approved semantics, exact-hit
queries, link rebuild on approve — and delegates every persistence concern to a
single-purpose repository. No runtime DDL; the migration must already be applied
(verified by the migration guard). core never imports Supabase.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.core.ports import WikiIngestPort, WikiQueryPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.postgres.connection import AsyncPgConnectionFactory
from knowledge_engine.infrastructure.wiki.postgres.files import FilesRepo, FileStorePort
from knowledge_engine.infrastructure.wiki.postgres.migration import ensure_schema
from knowledge_engine.infrastructure.wiki.postgres.repos import (
    EventsRepo,
    LinksRepo,
    PagesRepo,
    QueriesRepo,
    RevisionsRepo,
    RunsRepo,
    SearchRepo,
    SourcesRepo,
    TagsRepo,
    extract_wikilinks,
    normalize_query,
    sha256_text,
)
from knowledge_engine.schemas.wiki import IngestResult, WikiExtraction, WikiHit, WikiThesis, WikiVerdict

_PREFIX = "postgres://wiki/"
_TITLE = "postgres://wiki/{slug}"

#: author_kind stamped on this adapter's revisions.
_AUTHOR_KIND = "ingest"


class PostgresWikiStore(WikiQueryPort, WikiIngestPort):
    """Relational wiki over the v2 ``wiki`` schema (async, server-side FTS).

    Args:
        factory: An async connection factory (from :meth:`from_env`/from_dsn).
        file_store: Optional :class:`FileStorePort` used to persist articles.
        model: LLM model stamped on new revisions (purge-ability), if known.
    """

    def __init__(
        self,
        factory: AsyncPgConnectionFactory,
        *,
        file_store: FileStorePort | None = None,
        model: str | None = None,
    ) -> None:
        self._factory = factory
        self._model = model
        self._file_store = file_store
        self._files = FilesRepo()
        self._runs = RunsRepo()
        self._pages = PagesRepo()
        self._revisions = RevisionsRepo()
        self._links = LinksRepo()
        self._queries = QueriesRepo()
        self._events = EventsRepo()
        self._tags = TagsRepo()
        self._search = SearchRepo()
        self._sources = SourcesRepo()
        self._migrated = False

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 5) -> PostgresWikiStore:
        """Build a store against a Postgres DSN (async pool).

        Args:
            dsn: Postgres connection string.
            min_size: Minimum pool size.
            max_size: Maximum pool size.

        Returns:
            PostgresWikiStore: Configured store.
        """
        return cls(AsyncPgConnectionFactory.from_dsn(dsn, min_size=min_size, max_size=max_size))

    @classmethod
    def from_env(cls) -> PostgresWikiStore:
        """Build the store from ``DATABASE_URL`` (or ``SUPABASE_DB_URL``).

        Returns:
            PostgresWikiStore: Configured store.

        Raises:
            KnowledgeEngineError: When no DSN is set.
        """
        import os

        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if not dsn:
            raise KnowledgeEngineError("wiki_store_kind='postgres' needs DATABASE_URL (or SUPABASE_DB_URL)")
        return cls.from_dsn(dsn)

    async def close(self) -> None:
        """Close the async connection pool."""
        await self._factory.close()

    async def __aenter__(self) -> PostgresWikiStore:
        """Enter the async context manager.

        Returns:
            PostgresWikiStore: Self.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit the async context manager (close the pool).

        Args:
            *exc: Context-manager exception payload (unused).
        """
        await self.close()

    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[Any]:
        """Open a migration-verified transaction.

        Yields:
            Any: An async cursor inside a transaction.
        """
        if not self._migrated:
            async with self._factory.transaction() as cur:
                await ensure_schema(cur)
            self._migrated = True
        async with self._factory.transaction() as cur:
            yield cur

    # -- query (WikiQueryPort) ------------------------------------------------
    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Server-side search; exact-hit then strict-then-broad FTS.

        Args:
            query: Research query.
            limit: Maximum hits.

        Returns:
            list[WikiHit]: Ranked hits (empty when nothing matches).
        """
        norm = normalize_query(query)
        qsha = sha256_text(norm)
        async with self._txn() as cur:
            if norm:
                exact = await self._queries.exact_hit(cur, query_sha256=qsha)
                if exact is not None:
                    slug, tldr = exact
                    await self._queries.hit(cur, query_sha256=qsha)
                    return [
                        WikiHit(
                            slug=slug,
                            title=slug,
                            path=_TITLE.format(slug=slug),
                            score=10.0,
                            summary=str(tldr or ""),
                            query_sha256=qsha,
                        )
                    ]
            return await self._search.search(cur, query=query, limit=limit)

    async def read_page(self, slug: str) -> str | None:
        """Read the current approved revision's markdown by slug.

        Args:
            slug: Page slug (any kind).

        Returns:
            str | None: Page markdown, or None when absent.
        """
        async with self._txn() as cur:
            row = await self._revisions.read_by_slug(cur, slug=slug)
        return row[1] if row else None

    # -- ingest (WikiIngestPort) ----------------------------------------------
    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """File one rendered article as an approved source page (transactional).

        Args:
            thread_id: Run thread identifier.
            query: Original research query.
            article_path: Rendered ``article.md`` path.

        Returns:
            IngestResult: Verdict + write counts.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"postgres wiki ingest: article missing: {article_path}")
        from psycopg import errors as pg_errors  # lazy: postgres adapter is opt-in

        text = src.read_text(encoding="utf-8")
        digest = sha256_text(text)
        title = common.page_title(query)
        slug = common.slugify(query)
        norm = normalize_query(query)
        qsha = sha256_text(norm)

        async with self._txn() as cur:
            await self._runs.start(cur, thread_id=thread_id, query_text=query)

            if self._file_store is not None:
                article_ref = await self._file_store.put(text.encode("utf-8"), mime="text/markdown")
                await self._files.register(cur, article_ref, text.encode("utf-8"))
                await self._files.attach_run_file(
                    cur, thread_id=thread_id, role="article", file_sha256=article_ref.sha256
                )

            cross = await self._revisions.find_other_thread(cur, content_sha256=digest, thread_id=thread_id)
            if cross is not None:
                await self._events.log(cur, op="ingest", page_id=None, thread_id=thread_id, title=title)
                await self._runs.finish(cur, thread_id=thread_id, status="finished")
                return self._no_material(cross, digest)

            page_id = await self._pages.upsert(cur, kind="source", slug=slug)
            verdict = (
                WikiVerdict.UPDATE if await self._revisions.has_history(cur, page_id=page_id) else WikiVerdict.NEW
            )
            await self._pages.lock(cur, page_id=page_id)
            rev_no = await self._pages.next_rev_no(cur, page_id=page_id)

            fm: dict[str, Any] = {
                "@id": f"postgres://wiki/source/{slug}",
                "type": "source",
                "title": title,
                "thread": thread_id,
                "date": common.today(),
                "sha256": digest,
                "query_sha256": qsha,
                "confidence": "EXTRACTED",
                "tldr": common.tldr(text),
            }
            rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n## Article\n\n" + text.rstrip()

            try:
                rev_id = await self._revisions.insert(
                    cur,
                    page_id=page_id,
                    rev_no=rev_no,
                    state="draft",
                    title=title,
                    body_md=rendered,
                    frontmatter=fm,
                    tldr=fm["tldr"],
                    confidence="EXTRACTED",
                    author_kind=_AUTHOR_KIND,
                    thread_id=thread_id,
                    model=self._model,
                    word_count=len(text.split()),
                    content_sha256=digest,
                )
            except pg_errors.UniqueViolation:
                return self._no_material(f"{_PREFIX}{slug}", digest)
            await self._approve(
                cur, page_id=page_id, revision_id=rev_id, kind="source", slug=slug, wikilinks=extract_wikilinks(text)
            )
            await self._queries.record(cur, query_sha256=qsha, query_text=query, normalized_text=norm, page_id=page_id)
            await self._events.log(cur, op="ingest", page_id=page_id, thread_id=thread_id, title=title)
            await self._runs.finish(cur, thread_id=thread_id, status="finished")

        return IngestResult(verdict=verdict, page_path=f"{_PREFIX}{slug}", pages_touched=1, content_sha256=digest)

    async def file_answer(self, thread_id: str, query: str, article_path: str) -> str:
        """File a rendered answer as an approved ``query`` page.

        Args:
            thread_id: Run thread identifier.
            query: Original research query.
            article_path: Rendered ``article.md`` path.

        Returns:
            str: The filed query page locator.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"postgres wiki file_answer: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        digest = sha256_text(text)
        title = common.page_title(query)
        slug = common.slugify(query)
        norm = normalize_query(query)
        qsha = sha256_text(norm)
        fm: dict[str, Any] = {
            "@id": f"postgres://wiki/query/{slug}",
            "type": "query",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "confidence": "EXTRACTED",
            "query_sha256": qsha,
            "tldr": common.tldr(text),
        }
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + f"## Answer\n\n{text.rstrip()}\n"
        async with self._txn() as cur:
            await self._runs.start(cur, thread_id=thread_id, query_text=query)
            page_id = await self._pages.upsert(cur, kind="query", slug=slug)
            await self._pages.lock(cur, page_id=page_id)
            rev_no = await self._pages.next_rev_no(cur, page_id=page_id)
            rev_id = await self._revisions.insert(
                cur,
                page_id=page_id,
                rev_no=rev_no,
                state="draft",
                title=title,
                body_md=rendered,
                frontmatter=fm,
                tldr=fm["tldr"],
                confidence="EXTRACTED",
                author_kind=_AUTHOR_KIND,
                thread_id=thread_id,
                model=self._model,
                word_count=len(text.split()),
                content_sha256=digest,
            )
            await self._approve(cur, page_id=page_id, revision_id=rev_id, kind="query", slug=slug, wikilinks=[])
            await self._queries.record(cur, query_sha256=qsha, query_text=query, normalized_text=norm, page_id=page_id)
            await self._events.log(cur, op="query", page_id=page_id, thread_id=thread_id, title=title)
            await self._runs.finish(cur, thread_id=thread_id, status="finished")
        return f"{_PREFIX}{slug}"

    async def compile_extraction(
        self, thread_id: str, query: str, extraction: WikiExtraction, feedback: list[str]
    ) -> list[str]:
        """File concept/entity pages as DRAFT revisions only (never approved).

        A draft must NOT change ``current_revision_id`` or the status of an
        existing approved page. New tags are never created here (anti-drift).

        Args:
            thread_id: Run thread identifier.
            query: Original research query.
            extraction: Validated extraction payload.
            feedback: Rejection feedback to embed in each body.

        Returns:
            list[str]: Draft page locators.
        """
        source_slug = common.slugify(query)
        locs: list[str] = []
        names = [(name, "concept") for name in extraction.concepts] + [
            (name, "entity") for name in extraction.entities
        ]
        async with self._txn() as cur:
            await self._runs.start(cur, thread_id=thread_id, query_text=query)
            for name, kind in names:
                slug = common.slugify(name)
                body = [f"Distilled from [[{source_slug}]] (query: {common.page_title(query)})."]
                if extraction.summary:
                    body.append(f"\n{extraction.summary}")
                if feedback:
                    body.append("\n## Reviewer feedback to address\n" + "\n".join(f"- {line}" for line in feedback))
                body_md = "\n\n".join(body) + "\n"
                fm: dict[str, Any] = {
                    "@id": f"postgres://wiki/{kind}/{slug}",
                    "type": kind,
                    "title": name,
                    "date": common.today(),
                    "thread": thread_id,
                    "status": "draft",
                    "confidence": "INFERRED",
                    "sources": [source_slug],
                    "sha256": sha256_text(body_md),
                    "tldr": common.tldr(extraction.summary or name),
                }
                rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body_md
                page_id = await self._pages.upsert(cur, kind=kind, slug=slug)
                await self._pages.lock(cur, page_id=page_id)
                rev_no = await self._pages.next_rev_no(cur, page_id=page_id)
                await self._revisions.insert(
                    cur,
                    page_id=page_id,
                    rev_no=rev_no,
                    state="draft",
                    title=name,
                    body_md=rendered,
                    frontmatter=fm,
                    tldr=fm["tldr"],
                    confidence="INFERRED",
                    author_kind="llm",
                    thread_id=thread_id,
                    model=self._model,
                    word_count=len(body_md.split()),
                    content_sha256=fm["sha256"],
                )
                tag_id = await self._tags.find_id(cur, name=slug, facet="method")
                if tag_id is not None:
                    await self._tags.attach(cur, page_id=page_id, tag_id=tag_id, origin="llm")
                locs.append(f"{_PREFIX}drafts/{slug}")
            await self._events.log(cur, op="compile", page_id=None, thread_id=thread_id, title=query)
            await self._runs.finish(cur, thread_id=thread_id, status="finished")
        return locs

    async def file_thesis(self, thread_id: str, thesis: WikiThesis) -> str:
        """File a thesis verdict as an approved ``synthesis`` page.

        Args:
            thread_id: Run thread identifier.
            thesis: Validated thesis payload.

        Returns:
            str: The synthesis page locator.
        """
        slug = common.slugify(thesis.claim)
        title = common.page_title(thesis.claim)
        fm: dict[str, Any] = {
            "@id": f"postgres://wiki/synthesis/{slug}",
            "type": "synthesis",
            "title": title,
            "date": common.today(),
            "thread": thread_id,
            "confidence": "INFERRED",
            "tldr": common.tldr(thesis.verdict),
        }
        lines = [f"**Claim:** {thesis.claim}", "", "## Evidence for"]
        lines += [f"- {line}" for line in thesis.for_claims] or ["- (none recorded)"]
        lines += ["", "## Evidence against"]
        lines += [f"- {line}" for line in thesis.against_claims] or ["- (none recorded)"]
        if thesis.sources:
            lines += ["", "## Sources"] + [f"- [[{s}]]" for s in thesis.sources]
        if thesis.verdict:
            lines += ["", "## Verdict", thesis.verdict]
        body_text = "\n".join(lines) + "\n"
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body_text
        digest = sha256_text(rendered)
        async with self._txn() as cur:
            await self._runs.start(cur, thread_id=thread_id, query_text=thesis.claim)
            page_id = await self._pages.upsert(cur, kind="synthesis", slug=slug)
            await self._pages.lock(cur, page_id=page_id)
            rev_no = await self._pages.next_rev_no(cur, page_id=page_id)
            rev_id = await self._revisions.insert(
                cur,
                page_id=page_id,
                rev_no=rev_no,
                state="draft",
                title=title,
                body_md=rendered,
                frontmatter=fm,
                tldr=fm["tldr"],
                confidence="INFERRED",
                author_kind="llm",
                thread_id=thread_id,
                model=self._model,
                word_count=len(body_text.split()),
                content_sha256=digest,
            )
            await self._approve(
                cur, page_id=page_id, revision_id=rev_id, kind="synthesis", slug=slug, wikilinks=thesis.sources
            )
            await self._events.log(cur, op="thesis", page_id=page_id, thread_id=thread_id, title=title)
            await self._runs.finish(cur, thread_id=thread_id, status="finished")
        return f"{_PREFIX}{slug}"

    async def feedback_for(self, query: str) -> list[str]:
        """Return rejection feedback recorded against this query's pages.

        Args:
            query: Research query whose slug pages may have feedback.

        Returns:
            list[str]: Feedback lines (empty when none).
        """
        slug = common.slugify(query)
        async with self._txn() as cur:
            return await self._events.feedback(cur, thread_id=slug)

    async def record_feedback(self, slug: str, line: str) -> None:
        """Record a rejection feedback line for a query slug (test/review API).

        Args:
            slug: Query slug (kebab-case).
            line: Feedback line.
        """
        async with self._txn() as cur:
            await self._events.record_feedback(cur, thread_id=slug, line=line)

    async def record_sources(self, thread_id: str, sources: list[Any]) -> int:
        """Persist a run's resolved sources into ``wiki.sources``/revision_sources.

        Each source is upserted by (kind, external_id) and, when a current
        approved revision exists for the thread, linked as ``cited``. If a file
        store is configured, the source list is also stored as a run artifact.

        Args:
            thread_id: Run thread identifier.
            sources: Resolved :class:`SourceRecord` list.

        Returns:
            int: Number of source rows upserted.
        """
        if not sources:
            return 0
        count = 0
        async with self._txn() as cur:
            revision_id = await self._sources.current_revision(cur, thread_id=thread_id)
            for record in sources:
                source_id = await self._sources.upsert(cur, record)
                if revision_id is not None:
                    await self._sources.link(cur, revision_id=revision_id, source_id=source_id)
                count += 1
            if self._file_store is not None:
                payload = json.dumps([s.model_dump(mode="json") for s in sources], ensure_ascii=False).encode("utf-8")
                ref = await self._file_store.put(payload, mime="application/json")
                await self._files.register(cur, ref, payload)
                await self._files.attach_run_file(cur, thread_id=thread_id, role="sources", file_sha256=ref.sha256)
        return count

    # -- orchestration helpers -------------------------------------------------
    @staticmethod
    def _no_material(slug: str, digest: str) -> IngestResult:
        """Build a no-material verdict.

        Args:
            slug: Existing page slug (or path).
            digest: Content hash.

        Returns:
            IngestResult: No-material verdict, zero page writes.
        """
        path = slug if slug.startswith("postgres://") else f"{_PREFIX}{slug}"
        return IngestResult(verdict=WikiVerdict.NO_MATERIAL, page_path=path, pages_touched=0, content_sha256=digest)

    async def _approve(
        self,
        cur: Any,
        *,
        page_id: str,
        revision_id: str,
        kind: str,
        slug: str,
        wikilinks: list[str],
    ) -> None:
        """Approve a revision and rebuild the page's links.

        Args:
            cur: Active async cursor.
            page_id: Page id.
            revision_id: Revision to approve.
            kind: Page kind (ownership context).
            slug: Page slug (ownership context).
            wikilinks: Destination slugs to link.
        """
        await self._pages.lock(cur, page_id=page_id)
        await cur.execute("SELECT wiki.approve_revision(%s)", (revision_id,))
        await self._links.rebuild(cur, src_page_id=page_id, dst_slugs=wikilinks)


__all__ = ["PostgresWikiStore"]

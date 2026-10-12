"""PostgresWikiStore — relational wiki adapter on Supabase Postgres (issue #31).

Pages live in ``wiki_pages``; backlinks in ``wiki_links``; the diary in
``wiki_log``. Markdown stays canonical and human-readable inside ``body_md``
(frontmatter + body); ``frontmatter`` is mirrored as JSONB for SQL queries.
Search runs **server-side** — a single ranked FTS ``SELECT`` fetches only the
matched rows instead of listing + downloading every page like the Supabase
Storage adapter (supabase_store.py:123,131) — so a query costs O(1) reads, not
O(N). Adapter-only behind ``WikiQueryPort``/``WikiIngestPort``; the engine
builds it when ``wiki_store_kind='postgres'``.

The SHA ingest cache is the table itself (``thread`` + ``content_sha256``), so
no-material/cross-thread dedup is a lookup, not a sidecar file, and survives
across processes. ``psycopg`` is required (the ``store`` extra).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from knowledge_engine.core.exceptions import ConfigurationError, KnowledgeEngineError
from knowledge_engine.core.ports import VaultLintPort, WikiIngestPort, WikiQueryPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.lint_core import lint_pages
from knowledge_engine.schemas.wiki import IngestResult, LintReport, WikiExtraction, WikiHit, WikiThesis, WikiVerdict

_PAGE = "postgres://wiki/"

# -- schema (issue #31 v1); DDL runs idempotently on construction -------------
_CREATE_PAGES = (
    "CREATE TABLE IF NOT EXISTS wiki_pages ("
    "slug text primary key,"
    "page_type text not null,"
    "title text not null,"
    "body_md text not null,"
    "frontmatter jsonb not null default '{}'::jsonb,"
    "thread text,"
    "confidence text,"
    "status text not null default 'active',"
    "content_sha256 text not null,"
    "updated_at timestamptz not null default now())"
)
_CREATE_LINKS = "CREATE TABLE IF NOT EXISTS wiki_links (src text not null, dst text not null, primary key (src, dst))"
_CREATE_LOG = (
    "CREATE TABLE IF NOT EXISTS wiki_log (ts timestamptz not null default now(), "
    "op text not null, title text not null)"
)
_CREATE_TYPE_IDX = "CREATE INDEX IF NOT EXISTS wiki_pages_type_idx ON wiki_pages (page_type)"
_CREATE_SHA_IDX = "CREATE INDEX IF NOT EXISTS wiki_pages_sha_idx ON wiki_pages (content_sha256)"
_ADD_TSV = (
    "ALTER TABLE wiki_pages ADD COLUMN IF NOT EXISTS search_tsv tsvector "
    "generated always as (to_tsvector('english', title || ' ' || body_md)) stored"
)
_CREATE_TSV_IDX = "CREATE INDEX IF NOT EXISTS wiki_pages_tsv_idx ON wiki_pages USING gin (search_tsv)"

_DDL = (_CREATE_PAGES, _CREATE_LINKS, _CREATE_LOG, _CREATE_TYPE_IDX, _CREATE_SHA_IDX, _ADD_TSV, _CREATE_TSV_IDX)

_SEARCH = (
    "SELECT slug, title, body_md, frontmatter->>'tldr', frontmatter->>'query_sha256', "
    "ts_rank(search_tsv, websearch_to_tsquery('english', %s)) "
    "FROM wiki_pages WHERE search_tsv @@ websearch_to_tsquery('english', %s) AND status = 'active' "
    "ORDER BY 6 DESC, slug ASC LIMIT %s"
)
_READ = "SELECT body_md FROM wiki_pages WHERE slug = %s"
_EXISTS = "SELECT 1 FROM wiki_pages WHERE slug = %s"
_DEDUP_THREAD = "SELECT slug FROM wiki_pages WHERE thread = %s AND content_sha256 = %s"
_DEDUP_CROSS = "SELECT thread, slug FROM wiki_pages WHERE content_sha256 = %s AND thread <> %s"
_INSERT_PAGE = (
    "INSERT INTO wiki_pages (slug, page_type, title, body_md, frontmatter, thread, "
    "confidence, status, content_sha256) "
    "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) "
    "ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title, body_md=EXCLUDED.body_md, "
    "frontmatter=EXCLUDED.frontmatter, thread=EXCLUDED.thread, confidence=EXCLUDED.confidence, "
    "status=EXCLUDED.status, content_sha256=EXCLUDED.content_sha256, updated_at=now()"
)
_INSERT_LOG = "INSERT INTO wiki_log (op, title) VALUES (%s, %s)"
_INSERT_LINK = "INSERT INTO wiki_links (src, dst) VALUES (%s, %s) ON CONFLICT DO NOTHING"
_READ_FEEDBACK = "SELECT title FROM wiki_log WHERE op = 'feedback' AND title = %s"

# -- vault lint (deterministic, server-side) ----------------------------------
_LINT_PAGES = "SELECT slug, body_md FROM wiki_pages WHERE status = 'active'"
_LINT_LINKED = "SELECT DISTINCT dst FROM wiki_links"

_WIKILINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")


@contextmanager
def _raw_connect(dsn: str) -> Iterator[Any]:
    """Yield a fresh autocommitted connection and close it on exit.

    Args:
        dsn: Postgres connection string.

    Yields:
        Any: A psycopg connection.
    """
    import psycopg

    conn = psycopg.connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


class PostgresWikiStore(WikiQueryPort, WikiIngestPort):
    """Supabase Postgres-backed wiki (relational, durable, server-side search).

    One connection is opened per operation via ``connect`` (a DSN, a pool, or a
    fake in tests), so the store is thread-safe and never holds a connection
    across async awaits. Autocommit matches the file/Supabase adapters (which
    are also non-transactional); DDL runs at most once per instance, guarded by
    a lock.

    Args:
        connect: Callable returning an object usable as ``with X as conn`` to
            obtain a live psycopg v3 connection (a pool hand-off, a raw
            ``psycopg`` connection, or an in-memory fake in tests).
        ensure_schema: When True, apply the wiki DDL on construction.
    """

    def __init__(self, connect: Callable[[], Any], *, ensure_schema: bool = True) -> None:
        self._connect = connect
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        if ensure_schema:
            self.ensure_schema()

    @classmethod
    def from_dsn(cls, dsn: str, *, pool: bool = True) -> PostgresWikiStore:
        """Build a store against a Postgres DSN, optionally pooled.

        Args:
            dsn: Postgres connection string.
            pool: When True, wrap connections in a ``psycopg_pool``
                ``ConnectionPool`` so a store can serve concurrent runs.

        Returns:
            PostgresWikiStore: Configured store.

        Raises:
            ConfigurationError: When ``psycopg`` (or ``psycopg[pool]``) is absent.
        """
        if not pool:
            try:
                import psycopg  # noqa: F401 - capability check; used by _raw_connect
            except ImportError as exc:
                raise ConfigurationError(
                    "the postgres wiki adapter needs the 'store' extra: uv sync --extra store"
                ) from exc
            return cls(lambda: _raw_connect(dsn))
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise ConfigurationError("the postgres wiki adapter needs psycopg[pool] for pool=True") from exc
        pool_obj: Any = ConnectionPool(dsn, min_size=1, max_size=5, timeout=15)
        return cls(lambda: pool_obj.connection())

    @classmethod
    def from_env(cls) -> PostgresWikiStore:
        """Build the store from ``DATABASE_URL`` (or ``SUPABASE_DB_URL``).

        Returns:
            PostgresWikiStore: Configured store.

        Raises:
            ConfigurationError: When no DSN is set.
        """
        import os

        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if not dsn:
            raise ConfigurationError("wiki_store_kind='postgres' needs DATABASE_URL (or SUPABASE_DB_URL)")
        return cls.from_dsn(dsn)

    def close(self) -> None:
        """Release pooled resources (no-op when built without a pool)."""

    def __enter__(self) -> PostgresWikiStore:
        """Enter the context manager.

        Returns:
            PostgresWikiStore: Self.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit the context manager (release pooled resources).

        Args:
            *exc: Context-manager exception payload (unused).
        """
        self.close()

    # -- schema ---------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Apply the idempotent DDL at most once per instance (lock-guarded).

        Raises:
            KnowledgeEngineError: When the schema cannot be applied.
        """
        if self._schema_ready:
            return
        # Double-checked inside the lock: a concurrent first caller applies DDL.
        with self._schema_lock:
            if not self._schema_ready:
                try:
                    with self._cursor() as cur:
                        for statement in _DDL:
                            cur.execute(statement)
                except Exception as exc:  # noqa: BLE001 - a DB outage is a hard stop
                    raise KnowledgeEngineError(f"postgres wiki: schema init failed: {exc}") from exc
                self._schema_ready = True

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """Borrow a connection+cursor for one operation.

        ``connect()`` returns an object usable via ``with X as conn`` — a pool
        hand-off, a raw ``psycopg`` connection, or a fake in tests. Each borrow
        either returns the hand-off to the pool or closes the raw connection.

        Yields:
            Any: A psycopg cursor (autocommit).
        """
        with self._connect() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                yield cur

    # -- query ----------------------------------------------------------------
    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Server-side ranked FTS search: one query, only matching rows.

        Args:
            query: Research query string.
            limit: Maximum hits to return.

        Returns:
            list[WikiHit]: Ranked hits (empty when nothing matches).
        """
        if not common.terms(query):
            return []
        with self._cursor() as cur:
            cur.execute(_SEARCH, (query, query, limit))
            rows = cur.fetchall()
        hits: list[WikiHit] = []
        for slug, title, _body, summary, qsha, rank in rows:
            hits.append(
                WikiHit(
                    slug=slug,
                    title=title,
                    path=f"{_PAGE}{slug}",
                    score=float(rank),
                    summary=str(summary or ""),
                    query_sha256=str(qsha or ""),
                )
            )
        hits.sort(key=lambda h: (-h.score, h.slug))
        return hits

    async def read_page(self, slug: str) -> str | None:
        """Read one page's canonical markdown by slug.

        Args:
            slug: Page slug.

        Returns:
            str | None: Page text (frontmatter + body), or None when absent.
        """
        with self._cursor() as cur:
            cur.execute(_READ, (slug,))
            row = cur.fetchone()
        return str(row[0]) if row else None

    # -- ingest ---------------------------------------------------------------
    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """File one rendered article into ``wiki_pages``.

        Args:
            thread_id: Run thread identifier (page provenance).
            query: Original research query (title + slug source).
            article_path: Rendered ``article.md`` path.

        Returns:
            IngestResult: Verdict (new/update/no_material) + write counts.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"postgres wiki ingest: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        digest = common.sha256_text(text)
        title = common.page_title(query)
        slug = common.slugify(query)

        with self._cursor() as cur:
            cur.execute(_DEDUP_THREAD, (thread_id, digest))
            prior = cur.fetchone()
            if prior is not None:
                cur.execute(_INSERT_LOG, ("ingest", title))
                return IngestResult(
                    verdict=WikiVerdict.NO_MATERIAL,
                    page_path=f"{_PAGE}{prior[0]}",
                    pages_touched=0,
                    content_sha256=digest,
                )
            cur.execute(_DEDUP_CROSS, (digest, thread_id))
            cross = cur.fetchone()
            if cross is not None:
                cur.execute(_INSERT_LOG, ("ingest", f"{title} (duplicate of {cross[0]})"))
                return IngestResult(
                    verdict=WikiVerdict.NO_MATERIAL,
                    page_path=f"{_PAGE}{cross[1]}",
                    pages_touched=0,
                    content_sha256=digest,
                )
            cur.execute(_EXISTS, (slug,))
            verdict = WikiVerdict.UPDATE if cur.fetchone() is not None else WikiVerdict.NEW

            fm: dict[str, Any] = {
                "type": "source",
                "title": title,
                "thread": thread_id,
                "date": common.today(),
                "sha256": digest,
                "query_sha256": common.sha256_text(query),
                "confidence": "EXTRACTED",
                "tldr": common.tldr(text),
            }
            rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n## Article\n\n" + text.rstrip()
            cur.execute(
                _INSERT_PAGE,
                (slug, "source", title, rendered, json.dumps(fm), thread_id, "EXTRACTED", "active", digest),
            )
            cur.execute(_INSERT_LOG, ("ingest", title))
            for dst in _WIKILINK.findall(text):
                cur.execute(_INSERT_LINK, (slug, dst))
        return IngestResult(verdict=verdict, page_path=f"{_PAGE}{slug}", pages_touched=1, content_sha256=digest)

    async def file_answer(self, thread_id: str, query: str, article_path: str) -> str:
        """File a rendered answer as a ``query`` page.

        Args:
            thread_id: Run thread identifier (lineage).
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
        title = common.page_title(query)
        slug = common.slugify(query)
        fm: dict[str, Any] = {
            "type": "query",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "confidence": "EXTRACTED",
            "tldr": common.tldr(text),
        }
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + f"## Answer\n\n{text.rstrip()}\n"
        with self._cursor() as cur:
            cur.execute(
                _INSERT_PAGE,
                (
                    slug,
                    "query",
                    title,
                    rendered,
                    json.dumps(fm),
                    thread_id,
                    "EXTRACTED",
                    "active",
                    common.sha256_text(text),
                ),
            )
            cur.execute(_INSERT_LOG, ("query", title))
        return f"{_PAGE}{slug}"

    # -- compile (LLM extraction) --------------------------------------------
    async def compile_extraction(
        self, thread_id: str, query: str, extraction: WikiExtraction, feedback: list[str]
    ) -> list[str]:
        """File concept/entity pages as drafts (``status='draft'``).

        Args:
            thread_id: Run thread identifier (lineage).
            query: Original research query (context).
            extraction: Validated extraction payload.
            feedback: Rejection feedback lines addressed in each body.

        Returns:
            list[str]: Draft page locators.
        """
        source_slug = common.slugify(query)
        locs: list[str] = []
        names = [(name, "concept") for name in extraction.concepts] + [
            (name, "entity") for name in extraction.entities
        ]
        for name, kind in names:
            slug = common.slugify(name)
            body = [f"Distilled from [[{source_slug}]] (query: {common.page_title(query)})."]
            if extraction.summary:
                body.append(f"\n{extraction.summary}")
            if feedback:
                body.append("\n## Reviewer feedback to address\n" + "\n".join(f"- {line}" for line in feedback))
            body_text = "\n\n".join(body) + "\n"
            fm: dict[str, Any] = {
                "type": kind,
                "title": name,
                "date": common.today(),
                "thread": thread_id,
                "status": "draft",
                "confidence": "INFERRED",
                "sources": [source_slug],
                "sha256": common.sha256_text(body_text),
                "tldr": common.tldr(extraction.summary or name),
            }
            rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body_text
            with self._cursor() as cur:
                cur.execute(
                    _INSERT_PAGE,
                    (
                        slug,
                        kind,
                        name,
                        rendered,
                        json.dumps(fm),
                        thread_id,
                        "INFERRED",
                        "draft",
                        common.sha256_text(body_text),
                    ),
                )
            locs.append(f"{_PAGE}drafts/{slug}")
        with self._cursor() as cur:
            cur.execute(_INSERT_LOG, ("compile", f"{len(locs)} drafts | {common.page_title(query)}"))
        return locs

    async def file_thesis(self, thread_id: str, thesis: WikiThesis) -> str:
        """File a thesis verdict page as ``synthesis``.

        Args:
            thread_id: Run thread identifier (lineage).
            thesis: Validated thesis payload.

        Returns:
            str: The synthesis page locator.
        """
        slug = common.slugify(thesis.claim)
        fm: dict[str, Any] = {
            "type": "synthesis",
            "title": common.page_title(thesis.claim),
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
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + "\n".join(lines) + "\n"
        with self._cursor() as cur:
            cur.execute(
                _INSERT_PAGE,
                (
                    slug,
                    "synthesis",
                    fm["title"],
                    rendered,
                    json.dumps(fm),
                    thread_id,
                    "INFERRED",
                    "active",
                    common.sha256_text(rendered),
                ),
            )
            cur.execute(_INSERT_LOG, ("thesis", fm["title"]))
        return f"{_PAGE}{slug}"

    async def feedback_for(self, query: str) -> list[str]:
        """Return rejection feedback recorded in ``wiki_log`` for this query.

        Args:
            query: Research query whose slug pages may have feedback.

        Returns:
            list[str]: Feedback lines (empty when none).
        """
        slug = common.slugify(query)
        with self._cursor() as cur:
            cur.execute(_READ_FEEDBACK, (slug,))
            rows = cur.fetchall()
        return [str(row[0]) for row in rows]


class PostgresVaultLinter(VaultLintPort):
    """Deterministic vault lint over Postgres: one query, no N downloads.

    Reuses the shared :func:`lint_pages` core (frontmatter, broken links, thin
    pages) over an ``{slug: body_md}`` map read in a single ``SELECT``, while
    orphans come from the relational ``wiki_links`` table (the source of truth).
    ``index_drift`` is always empty: the table itself is the index (issue #31).
    """

    def __init__(self, store: PostgresWikiStore) -> None:
        """Create a PostgresVaultLinter over a store's connections.

        Args:
            store: A :class:`PostgresWikiStore` (shared connection factory).
        """
        self._store = store

    async def lint(self) -> LintReport:
        """Scan the ``wiki_pages``/``wiki_links`` tables and report findings.

        Returns:
            LintReport: Findings; ``ok`` is True when the vault is clean.
        """
        with self._store._cursor() as cur:
            cur.execute(_LINT_PAGES)
            pages = {slug: body for slug, body in cur.fetchall()}
            cur.execute(_LINT_LINKED)
            linked = {row[0] for row in cur.fetchall()}
        report = lint_pages(pages, root=_PAGE.rstrip("/"), index_text=None)
        orphans = [slug for slug in pages if slug not in linked]
        return LintReport(
            root=report.root,
            pages=report.pages,
            missing_frontmatter=report.missing_frontmatter,
            broken_links=report.broken_links,
            orphans=sorted(orphans),
            thin_pages=report.thin_pages,
            index_drift=[],
        )


__all__ = ["PostgresVaultLinter", "PostgresWikiStore"]

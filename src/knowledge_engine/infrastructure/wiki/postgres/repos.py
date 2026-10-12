"""Async repositories over the wiki v2 schema (one class per concern, SRP).

Each repository is stateless, receives the active **async** cursor, and ``await``s
its own statements. The store owns transactions; repos stay narrow and are
exercised against a live Postgres (no SQL fakes).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from knowledge_engine.schemas.wiki import WikiHit

_WIKILINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")
_VOLATILITY_TTL_DAYS: dict[str, int | None] = {"fast": 30, "slow": 180, "stable": None}


def extract_wikilinks(text: str) -> list[str]:
    """Return unique kebab-case [[wikilink]] targets in a body.

    Args:
        text: Page markdown.

    Returns:
        list[str]: Unique target slugs.
    """
    return list(dict.fromkeys(_WIKILINK.findall(text)))


def normalize_query(query: str) -> str:
    """Normalize a query for exact-hit hashing (lowercase, strip punctuation, collapse ws).

    Args:
        query: Raw query string.

    Returns:
        str: Normalized query.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", query.lower())).strip()


def sha256_text(text: str) -> str:
    """SHA256 hex digest of UTF-8 text.

    Args:
        text: Text to hash.

    Returns:
        str: Hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RunsRepo:
    """Start/finish research runs (provenance, SRP)."""

    _INSERT = (
        "INSERT INTO wiki.runs (thread_id, query_text, status, providers, settings) "
        "VALUES (%s, %s, 'finished', %s::jsonb, %s::jsonb) "
        "ON CONFLICT (thread_id) DO UPDATE SET query_text = EXCLUDED.query_text"
    )
    _FINISH = "UPDATE wiki.runs SET status = %s, finished_at = now(), n_sources = %s WHERE thread_id = %s"

    async def start(self, cur: Any, *, thread_id: str, query_text: str) -> None:
        """Insert a run, or refresh its query text on re-run.

        Args:
            cur: Active async cursor.
            thread_id: Run thread identifier.
            query_text: Original query.
        """
        await cur.execute(self._INSERT, (thread_id, query_text, "{}", "{}"))

    async def finish(self, cur: Any, *, thread_id: str, status: str, n_sources: int = 0) -> None:
        """Close a run.

        Args:
            cur: Active async cursor.
            thread_id: Run thread identifier.
            status: Terminal status.
            n_sources: Source count.
        """
        await cur.execute(self._FINISH, (status, n_sources, thread_id))


class PagesRepo:
    """Page identity/lifecycle (SRP)."""

    _UPSERT = (
        "INSERT INTO wiki.pages (kind, slug, status, lang, volatility) "
        "VALUES (%s, %s, 'draft', 'en', %s) "
        "ON CONFLICT (kind, slug) DO UPDATE SET lang = EXCLUDED.lang "
        "RETURNING id"
    )
    _LOCK = "SELECT 1 FROM wiki.pages WHERE id = %s FOR UPDATE"
    _FIND = "SELECT id, current_revision_id FROM wiki.pages WHERE kind = %s AND slug = %s"
    _MAX_REV = "SELECT coalesce(max(rev_no), 0) FROM wiki.revisions WHERE page_id = %s"

    async def upsert(self, cur: Any, *, kind: str, slug: str, volatility: str = "slow") -> str:
        """Upsert a page (unique by kind+slug) and return its id.

        Args:
            cur: Active async cursor.
            kind: Page kind.
            slug: Page slug.
            volatility: slow/fast/stable (drives review_after TTL).

        Returns:
            str: Page id.
        """
        await cur.execute(self._UPSERT, (kind, slug, volatility))
        row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    async def find(self, cur: Any, *, kind: str, slug: str) -> tuple[str, str | None] | None:
        """Return (id, current_revision_id) for a kind+slug, or None.

        Args:
            cur: Active async cursor.
            kind: Page kind.
            slug: Page slug.

        Returns:
            tuple[str, str | None] | None: (id, current_revision_id) or None.
        """
        await cur.execute(self._FIND, (kind, slug))
        row = await cur.fetchone()
        return (str(row[0]), str(row[1]) if row[1] else None) if row else None

    async def lock(self, cur: Any, *, page_id: str) -> None:
        """Lock the page row to serialize rev_no assignment.

        Args:
            cur: Active async cursor.
            page_id: Page id.
        """
        await cur.execute(self._LOCK, (page_id,))

    async def next_rev_no(self, cur: Any, *, page_id: str) -> int:
        """Return the next revision number for a page.

        Args:
            cur: Active async cursor.
            page_id: Page id.

        Returns:
            int: Next rev_no (>= 1).
        """
        await cur.execute(self._MAX_REV, (page_id,))
        row = await cur.fetchone()
        return int(row[0]) + 1


class RevisionsRepo:
    """Versioned bodies + content de-dup + approval (SRP)."""

    _INSERT = (
        "INSERT INTO wiki.revisions "
        "(page_id, rev_no, state, title, body_md, frontmatter, tldr, confidence, "
        " author_kind, thread_id, model, word_count, content_sha256) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id"
    )
    _READ = (
        "SELECT r.title, r.body_md, r.frontmatter, r.thread_id "
        "FROM wiki.pages p JOIN wiki.revisions r ON r.id = p.current_revision_id "
        "WHERE p.kind = %s AND p.slug = %s AND p.status = 'active'"
    )
    _READ_SLUG = (
        "SELECT r.title, r.body_md, r.frontmatter, r.thread_id "
        "FROM wiki.pages p JOIN wiki.revisions r ON r.id = p.current_revision_id "
        "WHERE p.slug = %s AND p.status = 'active' LIMIT 1"
    )
    _HAS_HISTORY = "SELECT 1 FROM wiki.revisions WHERE page_id = %s LIMIT 1"
    _BY_SHA = (
        "SELECT p.slug FROM wiki.revisions r JOIN wiki.pages p ON p.id = r.page_id "
        "WHERE r.content_sha256 = %s AND r.thread_id IS NOT NULL AND r.thread_id <> %s "
        "LIMIT 1"
    )

    async def insert(
        self,
        cur: Any,
        *,
        page_id: str,
        rev_no: int,
        state: str,
        title: str,
        body_md: str,
        frontmatter: dict[str, Any],
        tldr: str | None,
        confidence: str | None,
        author_kind: str,
        thread_id: str | None,
        model: str | None,
        word_count: int | None,
        content_sha256: str,
    ) -> str:
        """Insert a revision and return its id.

        Args:
            cur: Active async cursor.
            page_id: Owning page id.
            rev_no: Revision number (>= 1).
            state: draft/approved/rejected/superseded.
            title: Page title.
            body_md: Canonical markdown.
            frontmatter: Frontmatter mapping (JSONB).
            tldr: One-line summary.
            confidence: EXTRACTED/INFERRED/AMBIGUOUS.
            author_kind: ingest/llm/human.
            thread_id: Producing thread or None.
            model: Producing LLM model, if known.
            word_count: Body word count, if known.
            content_sha256: Body hash.

        Returns:
            str: Revision id.
        """
        await cur.execute(
            self._INSERT,
            (
                page_id,
                rev_no,
                state,
                title,
                body_md,
                json.dumps(frontmatter),
                tldr,
                confidence,
                author_kind,
                thread_id,
                model,
                word_count,
                content_sha256,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    async def read_page(self, cur: Any, *, kind: str, slug: str) -> tuple[str, str, dict[str, Any], str | None] | None:
        """Read the current approved revision of a page.

        Args:
            cur: Active async cursor.
            kind: Page kind.
            slug: Page slug.

        Returns:
            tuple[str, str, dict[str, Any], str | None] | None: (title, body_md,
            frontmatter, thread_id), or None when absent.
        """
        await cur.execute(self._READ, (kind, slug))
        row = await cur.fetchone()
        return (str(row[0]), str(row[1]), row[2], str(row[3]) if row[3] else None) if row else None

    async def read_by_slug(self, cur: Any, *, slug: str) -> tuple[str, str, dict[str, Any], str | None] | None:
        """Read the current approved revision of any active page with a slug.

        Args:
            cur: Active async cursor.
            slug: Page slug (any kind).

        Returns:
            tuple[str, str, dict[str, Any], str | None] | None: (title, body_md,
            frontmatter, thread_id), or None when absent.
        """
        await cur.execute(self._READ_SLUG, (slug,))
        row = await cur.fetchone()
        return (str(row[0]), str(row[1]), row[2], str(row[3]) if row[3] else None) if row else None

    async def has_history(self, cur: Any, *, page_id: str) -> bool:
        """Return True when the page has at least one revision already.

        Args:
            cur: Active async cursor.
            page_id: Page id.

        Returns:
            bool: Whether the page has a prior revision.
        """
        await cur.execute(self._HAS_HISTORY, (page_id,))
        return await cur.fetchone() is not None

    async def find_other_thread(self, cur: Any, *, content_sha256: str, thread_id: str) -> str | None:
        """Return a page slug with identical content under another thread.

        Args:
            cur: Active async cursor.
            content_sha256: Body hash.
            thread_id: Current thread (excluded).

        Returns:
            str | None: Slug or None.
        """
        await cur.execute(self._BY_SHA, (content_sha256, thread_id))
        row = await cur.fetchone()
        return str(row[0]) if row else None


class LinksRepo:
    """Typed graph edges, rebuilt on approve (SRP)."""

    _DELETE = "DELETE FROM wiki.links WHERE src_page_id = %s"
    _INSERT = "INSERT INTO wiki.links (src_page_id, dst_slug, dst_page_id, rel) VALUES (%s, %s, %s, %s)"
    _PAGE_BY_SLUG = "SELECT id FROM wiki.pages WHERE slug = %s LIMIT 1"
    _BACKFILL = (
        "UPDATE wiki.links l SET dst_page_id = p.id FROM wiki.pages p "
        "WHERE l.dst_slug = p.slug AND l.dst_page_id IS NULL"
    )
    _DST_BY_PAGE = "SELECT DISTINCT dst_page_id FROM wiki.links WHERE dst_page_id IS NOT NULL"

    async def resolve(self, cur: Any, *, slug: str) -> str | None:
        """Resolve a slug to a page id, or None when the page does not exist.

        Args:
            cur: Active async cursor.
            slug: Destination slug.

        Returns:
            str | None: Page id or None.
        """
        await cur.execute(self._PAGE_BY_SLUG, (slug,))
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def rebuild(self, cur: Any, *, src_page_id: str, dst_slugs: list[str], rel: str = "related") -> None:
        """Delete the page's old edges and insert fresh ones (resolving dst ids).

        Args:
            cur: Active async cursor.
            src_page_id: Source page id.
            dst_slugs: Destination slugs from [[wikilinks]].
            rel: Edge type.
        """
        await cur.execute(self._DELETE, (src_page_id,))
        await cur.execute(self._BACKFILL)
        for slug in dst_slugs:
            dst_id = await self.resolve(cur, slug=slug)
            await cur.execute(self._INSERT, (src_page_id, slug, dst_id, rel))

    async def linked_pages(self, cur: Any) -> set[str]:
        """Return the set of page ids that have at least one inbound link.

        Args:
            cur: Active async cursor.

        Returns:
            set[str]: Linked page ids.
        """
        await cur.execute(self._DST_BY_PAGE)
        return {str(row[0]) for row in await cur.fetchall()}


class QueriesRepo:
    """Exact-hit cache keyed by NORMALIZED query sha (SRP)."""

    _UPSERT = (
        "INSERT INTO wiki.queries (query_sha256, query_text, normalized_text, page_id) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (query_sha256) DO UPDATE SET last_hit_at = now()::timestamptz"
    )
    _EXACT = (
        "SELECT p.slug, r.tldr FROM wiki.queries q "
        "JOIN wiki.pages p ON p.id = q.page_id "
        "JOIN wiki.revisions r ON r.id = p.current_revision_id "
        "WHERE q.query_sha256 = %s AND p.status = 'active' LIMIT 1"
    )
    _HIT = "UPDATE wiki.queries SET hit_count = hit_count + 1, last_hit_at = now() WHERE query_sha256 = %s"

    async def record(
        self, cur: Any, *, query_sha256: str, query_text: str, normalized_text: str, page_id: str
    ) -> None:
        """Register a query row (idempotent).

        Args:
            cur: Active async cursor.
            query_sha256: Normalized hash.
            query_text: Raw query.
            normalized_text: Normalized query.
            page_id: Page that answered it.
        """
        await cur.execute(self._UPSERT, (query_sha256, query_text, normalized_text, page_id))

    async def exact_hit(self, cur: Any, *, query_sha256: str) -> tuple[str, str] | None:
        """Return (slug, tldr) of a page matching an exact normalized query.

        Args:
            cur: Active async cursor.
            query_sha256: Normalized hash.

        Returns:
            tuple[str, str] | None: (slug, tldr) or None.
        """
        await cur.execute(self._EXACT, (query_sha256,))
        row = await cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    async def hit(self, cur: Any, *, query_sha256: str) -> None:
        """Bump the hit counter (an exact cache hit saved a live run).

        Args:
            cur: Active async cursor.
            query_sha256: Normalized hash.
        """
        await cur.execute(self._HIT, (query_sha256,))


class EventsRepo:
    """Append-only event diary; feedback in detail->>'feedback' (SRP)."""

    _FEEDBACK = (
        "INSERT INTO wiki.events (op, thread_id, title, detail) "
        "VALUES ('feedback', %s, %s, jsonb_build_object('feedback', %s::text))"
    )
    _READ_FEEDBACK = (
        "SELECT detail->>'feedback' FROM wiki.events WHERE op = 'feedback' AND thread_id = %s ORDER BY ts, id"
    )
    _INGEST = "INSERT INTO wiki.events (op, page_id, thread_id, title) VALUES (%s, %s, %s, %s)"

    async def record_feedback(self, cur: Any, *, thread_id: str, line: str) -> None:
        """Record one rejection feedback line for a thread.

        Args:
            cur: Active async cursor.
            thread_id: Thread id.
            line: Feedback line.
        """
        await cur.execute(self._FEEDBACK, (thread_id, line[:200], line))

    async def feedback(self, cur: Any, *, thread_id: str) -> list[str]:
        """Return feedback lines for a thread.

        Args:
            cur: Active async cursor.
            thread_id: Thread id.

        Returns:
            list[str]: Feedback lines.
        """
        await cur.execute(self._READ_FEEDBACK, (thread_id,))
        return [str(row[0]) for row in await cur.fetchall()]

    async def log(self, cur: Any, *, op: str, page_id: str | None, thread_id: str | None, title: str | None) -> None:
        """Append an ingest/compile/thesis/query event.

        Args:
            cur: Active async cursor.
            op: Operation name.
            page_id: Optional page id.
            thread_id: Optional thread id.
            title: Optional page title.
        """
        await cur.execute(self._INGEST, (op, page_id, thread_id, title))


class TagsRepo:
    """Controlled vocabulary: match existing tags only, never auto-create (SRP)."""

    _FIND = "SELECT id FROM wiki.tags WHERE name = %s AND facet = %s"
    _ATTACH = "INSERT INTO wiki.page_tags (page_id, tag_id, origin) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"

    async def find_id(self, cur: Any, *, name: str, facet: str = "method") -> str | None:
        """Return an existing tag's id, or None (new tags require an explicit path).

        Args:
            cur: Active async cursor.
            name: Tag name.
            facet: Tag facet.

        Returns:
            str | None: Tag id or None.
        """
        await cur.execute(self._FIND, (name, facet))
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def attach(self, cur: Any, *, page_id: str, tag_id: str, origin: str = "llm") -> None:
        """Attach a page to an existing tag.

        Args:
            cur: Active async cursor.
            page_id: Page id.
            tag_id: Tag id.
            origin: llm/human/rule.
        """
        await cur.execute(self._ATTACH, (page_id, tag_id, origin))


class SourcesRepo:
    """Persist research sources + link them to the producing revision (SRP)."""

    _CURRENT_REV = (
        "SELECT r.id FROM wiki.revisions r JOIN wiki.pages p ON p.id = r.page_id "
        "WHERE r.thread_id = %s AND r.state = 'approved' ORDER BY r.created_at DESC LIMIT 1"
    )
    _UPSERT = (
        "INSERT INTO wiki.sources (kind, external_id, title, url, authors, year, abstract, "
        " citation_count, venue, provider, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (kind, external_id) DO UPDATE SET title = EXCLUDED.title "
        "RETURNING id"
    )
    _LINK = (
        "INSERT INTO wiki.revision_sources (revision_id, source_id, role) "
        "VALUES (%s, %s, 'cited') ON CONFLICT DO NOTHING"
    )

    @staticmethod
    def _kind(record: Any) -> str:
        """Derive the wiki.sources ``kind`` from a source record.

        Args:
            record: A SourceRecord.

        Returns:
            str: doi/arxiv/pmid/other.
        """
        name = str(getattr(record, "source", "")).lower()
        if "arxiv" in name:
            return "arxiv"
        if "pmid" in name or "pubmed" in name:
            return "pmid"
        if getattr(record, "doi", None):
            return "doi"
        return "other"

    async def current_revision(self, cur: Any, *, thread_id: str) -> str | None:
        """Return the latest approved revision produced by a thread, if any.

        Args:
            cur: Active async cursor.
            thread_id: Run thread identifier.

        Returns:
            str | None: Revision id or None.
        """
        await cur.execute(self._CURRENT_REV, (thread_id,))
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def upsert(self, cur: Any, record: Any) -> str:
        """Upsert a source by (kind, external_id) and return its id.

        Args:
            cur: Active async cursor.
            record: A SourceRecord.

        Returns:
            str: Source id.
        """
        metadata = record.model_dump(mode="json") if hasattr(record, "model_dump") else str(record)
        await cur.execute(
            self._UPSERT,
            (
                self._kind(record),
                str(record.id),
                str(record.title),
                str(record.pdf_url or ""),
                list(getattr(record, "authors", []) or []),
                getattr(record, "year", None),
                str(getattr(record, "abstract", None) or "") or None,
                int(getattr(record, "citation_count", 0) or 0),
                str(getattr(record, "venue", None) or "") or None,
                str(getattr(record, "source", "")).split(".")[-1],
                json.dumps(metadata),
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    async def link(self, cur: Any, *, revision_id: str, source_id: str) -> None:
        """Cite a source on a revision (role=cited).

        Args:
            cur: Active async cursor.
            revision_id: Revision id.
            source_id: Source id.
        """
        await cur.execute(self._LINK, (revision_id, source_id))


class SearchRepo:
    """Server-side filtered search via ``wiki.search_pages_filtered`` (SRP)."""

    _STMT = (
        "SELECT slug, kind, title, tldr, confidence, query_sha256, rank, matched_broad "
        "FROM wiki.search_pages_filtered(p_query => %s, p_broad => %s, p_limit => %s)"
    )

    async def search(self, cur: Any, *, query: str, limit: int) -> list[WikiHit]:
        """Strict search first; fall back to broad only when strict is empty.

        Args:
            cur: Active async cursor.
            query: Research query.
            limit: Maximum hits.

        Returns:
            list[WikiHit]: Ranked hits (strict, else broad).
        """
        await cur.execute(self._STMT, (query, False, limit))
        rows = await cur.fetchall()
        matched_broad = False
        if not rows:
            await cur.execute(self._STMT, (query, True, limit))
            rows = await cur.fetchall()
            matched_broad = True
        return self._map(rows, matched_broad=matched_broad)

    @staticmethod
    def _map(rows: list[tuple[Any, ...]], *, matched_broad: bool) -> list[WikiHit]:
        """Map result rows to hits, applying a Python-side score threshold.

        Args:
            rows: Raw rows (body_md is never selected).
            matched_broad: Whether these came from the broad fallback.

        Returns:
            list[WikiHit]: Hits above the score floor.
        """
        hits: list[WikiHit] = []
        for slug, kind, title, tldr, _confidence, qsha, rank, _broad in rows:
            score = float(rank or 0.0)
            if score <= 0.0:
                continue
            hits.append(
                WikiHit(
                    slug=str(slug),
                    title=str(title),
                    path=f"postgres://wiki/{kind}/{slug}",
                    score=score,
                    summary=str(tldr or ""),
                    query_sha256=str(qsha or ""),
                )
            )
        return hits


__all__ = [
    "EventsRepo",
    "LinksRepo",
    "PagesRepo",
    "QueriesRepo",
    "RevisionsRepo",
    "RunsRepo",
    "SearchRepo",
    "SourcesRepo",
    "TagsRepo",
    "extract_wikilinks",
    "normalize_query",
    "sha256_text",
]

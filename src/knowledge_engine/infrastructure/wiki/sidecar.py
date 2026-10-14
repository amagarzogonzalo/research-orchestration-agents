"""WikiSidecar — derivable SQLite + FTS5 index over a local wiki vault.

Loop 4 (per docs/LLM_WIKI.md): the markdown vault stays the source of truth;
the sidecar is a rebuildable cache for scale (``index.md`` recall saturates
past ~100 pages). Single file ``.wiki.db`` next to the vault:

    notes(slug, path, title, type, date) | notes_fts (FTS5) | links | tags

Graceful degrade: when the sqlite build lacks FTS5 the adapter raises and
:class:`FileWikiStore` falls back to scan scoring — never hard-fail a run
for an optional accelerator.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.infrastructure.wiki import common

DB_NAME = ".wiki.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    slug TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT,
    date TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(slug, title, body);
CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    slug TEXT NOT NULL,
    tag TEXT NOT NULL
);
"""


class WikiSidecar:
    """Build/query/search a rebuildable FTS5 index over the vault.

    Args:
        root: Wiki root directory (``.wiki.db`` lives beside it).
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.db_path = self.root / DB_NAME

    def fts_supported(self) -> bool:
        """Return True when this sqlite build provides the FTS5 module.

        Returns:
            bool: Availability (probed cheaply on an in-memory database).
        """
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            conn.close()
            return True
        except (sqlite3.Error, AttributeError):
            return False

    def build(self) -> None:
        """(Re)build the index from the current vault contents.

        Idempotent and derivable: deleting the file and re-building yields
        identical content. Raises :class:`KnowledgeEngineError` when the
        sqlite build lacks FTS5 (the caller falls back to scan scoring).

        Raises:
            KnowledgeEngineError: When FTS5 is unavailable.
        """
        if not self.fts_supported():
            raise KnowledgeEngineError("sqlite build lacks FTS5; wiki sidecar unavailable")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(_SCHEMA)
            for dir_name in common.PAGE_DIRS:
                d = self.root / dir_name
                if not d.is_dir():
                    continue
                for page in sorted(d.glob("*.md")):
                    self._index_page(conn, dir_name, page)
            conn.commit()
        finally:
            conn.close()

    def _index_page(self, conn: sqlite3.Connection, dir_name: str, page: Path) -> None:
        """Insert one page (notes row + FTS row + links/tags rows).

        Args:
            conn: Open database connection.
            dir_name: Vault subdirectory (``sources``/``concepts``/...).
            page: The markdown page.
        """
        text = page.read_text(encoding="utf-8")
        fm, body = common.split_frontmatter(text)
        slug = self._class_slug(page.stem, dir_name)
        conn.execute(
            "INSERT OR REPLACE INTO notes (slug, path, title, type, date) VALUES (?, ?, ?, ?, ?)",
            (
                slug,
                str(page),
                str(fm.get("title") or page.stem),
                str(fm.get("type") or ""),
                str(fm.get("date") or ""),
            ),
        )
        conn.execute(
            "INSERT INTO notes_fts (slug, title, body) VALUES (?, ?, ?)",
            (slug, str(fm.get("title") or page.stem), body),
        )
        for target in _wikilinks(text):
            conn.execute("INSERT INTO links (src, dst) VALUES (?, ?)", (slug, target))
        for tag in _frontmatter_tags(fm):
            conn.execute("INSERT INTO tags (slug, tag) VALUES (?, ?)", (slug, tag))

    def _class_slug(self, stem: str, dir_name: str) -> str:
        """Namespaced slug so identical stems across dirs coexist.

        Args:
            stem: Filename stem.
            dir_name: Vault subdirectory.

        Returns:
            str: ``<dir>/<stem>`` namespaced slug.
        """
        return f"{dir_name}/{stem}"

    def insert_page(self, dir_name: str, page: Path) -> None:
        """Incrementally index one page into an existing sidecar.

        Args:
            dir_name: Vault subdirectory.
            page: The page to add/update.
        """
        if not self.db_path.exists():
            self.build()
            return
        conn = sqlite3.connect(self.db_path)
        try:
            slug = self._class_slug(page.stem, dir_name)
            conn.execute("DELETE FROM notes WHERE slug = ?", (slug,))
            conn.execute("DELETE FROM notes_fts WHERE slug = ?", (slug,))
            conn.execute("DELETE FROM links WHERE src = ?", (slug,))
            conn.execute("DELETE FROM tags WHERE slug = ?", (slug,))
            self._index_page(conn, dir_name, page)
            conn.commit()
        finally:
            conn.close()

    def search(self, query: str, *, limit: int = 3) -> list[tuple[str, str, float]]:
        """BM25-ranked FTS5 query over titles + bodies.

        Args:
            query: Free-text query.
            limit: Maximum results.

        Returns:
            list[tuple[str, str, float]]: (namespaced slug, path, bm25 score)
            ranked best-first; empty when the db does not exist.
        """
        if not self.db_path.exists() or not query.strip():
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            ranked = conn.execute(
                "SELECT slug, bm25(notes_fts) FROM notes_fts WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
                (_fts_query(query), limit),
            ).fetchall()
            out: list[tuple[str, str, float]] = []
            for slug, score in ranked:
                row = conn.execute("SELECT path FROM notes WHERE slug = ?", (str(slug),)).fetchone()
                if row is not None:
                    out.append((str(slug), str(row[0]), float(score or 0.0)))
            return out
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def hybrid_search(self, query: str, *, limit: int = 3, rrf_k: int = 60) -> list[tuple[str, str, float]]:
        """Reciprocal-rank fusion of FTS5 BM25 + deterministic scan scoring.

        Fuses the two rankers by position (RRF, ``k=60``), never by raw score
        (BM25 and term-overlap distributions are incomparable). Deterministic:
        both input lists are sorted before fusion. Pages findable by either
        signature survive; the union is ranked.

        Args:
            query: Free-text query.
            limit: Maximum results.
            rrf_k: RRF constant.

        Returns:
            list[tuple[str, str, float]]: (slug, path, fused score), ranked.
        """
        if not self.db_path.exists() or not query.strip() or not self.fts_supported():
            return []
        fts = self.search(query, limit=limit * 4)
        scan = _scan_scores(self.root, query)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT slug, path FROM notes").fetchall()
            by_slug: dict[str, str] = {}
            by_stem: dict[str, list[str]] = {}
            for slug, path in rows:
                by_slug[str(slug)] = str(path)
                by_stem.setdefault(str(slug).rsplit("/", 1)[-1], []).append(str(slug))
        finally:
            conn.close()
        merged: dict[str, float] = {}
        for rank, (slug, _path, _score) in enumerate(fts, start=1):
            merged[slug] = merged.get(slug, 0.0) + 1.0 / (rrf_k + rank)
        for rank, (stem, _score) in enumerate(scan, start=1):
            # Scan keys are bare stems while FTS keys are namespaced
            # (``sources/rag`` vs ``rag``); fuse on the namespaced slug so one
            # page never occupies two result slots.
            for slug in by_stem.get(stem, [stem]):
                merged[slug] = merged.get(slug, 0.0) + 1.0 / (rrf_k + rank)
        ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
        out: list[tuple[str, str, float]] = []
        for slug, score in ranked:
            out.append((slug, by_slug.get(slug, slug), score))
            if len(out) >= limit:
                break
        return out

    def counts(self) -> dict[str, int]:
        """Row counts per table (test/inspection helper).

        Returns:
            dict[str, int]: notes/links/tags counts (zeros when absent).
        """
        if not self.db_path.exists():
            return {"notes": 0, "links": 0, "tags": 0}
        conn = sqlite3.connect(self.db_path)
        try:
            out = {}
            for table in ("notes", "links", "tags"):
                out[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
            return out
        finally:
            conn.close()


def _scan_scores(root: Path, query: str) -> list[tuple[str, float]]:
    """Deterministic term-overlap scoring over the vault (scan signature).

    Args:
        root: Wiki root.
        query: Query text.

    Returns:
        list[tuple[str, float]]: (slug, score), best-first.
    """
    query_terms = common.terms(query)
    if not query_terms:
        return []
    scored: list[tuple[str, float]] = []
    if root.is_dir():
        for name in common.PAGE_DIRS:
            d = root / name
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                text = p.read_text(encoding="utf-8")
                fm, body = common.split_frontmatter(text)
                title = str(fm.get("title") or p.stem)
                value = common.score(query_terms, title, body)
                if value > 0.0:
                    scored.append((p.stem, value))
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored


def _wikilinks(text: str) -> list[str]:
    """Extract [[wikilink]] targets.

    Args:
        text: Page text.

    Returns:
        list[str]: Link targets.
    """
    import re

    return re.findall(r"\[\[([^\]|#]+)", text)


def _frontmatter_tags(fm: dict[str, Any]) -> list[str]:
    """Extract frontmatter tags (list or string form).

    Args:
        fm: Parsed frontmatter mapping.

    Returns:
        list[str]: Tags.
    """
    raw = fm.get("tags")
    if isinstance(raw, list):
        return [str(t) for t in raw if str(t).strip()]
    if isinstance(raw, str) and raw.strip():
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _fts_query(query: str) -> str:
    """FTS5-safe query string (quoted OR-terms, no operator injection).

    Args:
        query: Raw query.

    Returns:
        str: FTS5 MATCH expression.
    """
    words = [w for w in query.split() if w.strip()]
    return " OR ".join(f'"{w}"' for w in words) if words else '""'


__all__ = ["DB_NAME", "WikiSidecar"]

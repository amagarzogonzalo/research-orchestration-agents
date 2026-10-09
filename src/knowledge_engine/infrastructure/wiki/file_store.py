"""FileWikiStore — local markdown wiki adapter (ADR wiki-loop0).

The Karpathy three-layer pattern, files-only: raw sources stay immutable,
the wiki lives under ``<root>`` (``sources/`` + ``synthesis/`` pages plus
``index.md``/``log.md``/``overview.md``), and this adapter is the LLM-owned
layer's deterministic Loop 0 implementation — no LLM calls, no embeddings.

Query is index/title scoring; ingest files the rendered article with a
SHA256 cache (unchanged sources never rewrite pages — No-material verdict
logs only) and cross-references the new page to existing related pages via
``[[wikilinks]]``. Close-never-delete: nothing is ever removed.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.core.ports import WikiIngestPort, WikiQueryPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.schemas.wiki import IngestResult, WikiHit, WikiVerdict

_RELATED_TERMS = 2
_RELATED_MAX = 3


class FileWikiStore(WikiQueryPort, WikiIngestPort):
    """Local markdown wiki: query by scoring, ingest with verdicts + cache.

    Args:
        root: Wiki root directory (created on demand). Defaults to
            ``<store_root>/wiki`` so the wiki moves with the store.
    """

    def __init__(self, root: str | Path = "research_store/wiki") -> None:
        self.root = Path(root)

    # -- layout ---------------------------------------------------------------
    def _ensure_layout(self) -> None:
        """Create the wiki skeleton (dirs + index + log + overview) on demand.

        Idempotent: steady state is zero writes; missing files are created
        with their headings only.
        """
        for name in common.PAGE_DIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        index = self.root / common.INDEX_NAME
        if not index.exists():
            index.write_text("# Knowledge Base Index\n\n## Sources\n", encoding="utf-8")
        log = self.root / common.LOG_NAME
        if not log.exists():
            log.write_text("# Wiki Log\n", encoding="utf-8")
        overview = self.root / common.OVERVIEW_NAME
        if not overview.exists():
            overview.write_text("# Overview\n", encoding="utf-8")

    def _pages(self) -> dict[str, Path]:
        """Enumerate wiki pages (index/log/overview excluded by design).

        Returns:
            dict[str, Path]: Slug -> page path.
        """
        pages: dict[str, Path] = {}
        for name in common.PAGE_DIRS:
            d = self.root / name
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                pages.setdefault(p.stem, p)
        return pages

    # -- query ----------------------------------------------------------------
    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Return the best-matching wiki pages for a query.

        Args:
            query: Research query string.
            limit: Maximum hits to return.

        Returns:
            list[WikiHit]: Ranked hits, best first (empty when nothing matches).
        """
        self._ensure_layout()
        query_terms = common.terms(query)
        if not query_terms:
            return []
        summaries = self._index_summaries()
        hits: list[WikiHit] = []
        for slug, path in self._pages().items():
            text = path.read_text(encoding="utf-8")
            fm, body = common.split_frontmatter(text)
            title = common.fm_title(fm, path)
            value = common.score(query_terms, title, body)
            if value <= 0.0:
                continue
            qsha = str(fm.get("query_sha256") or "")
            hits.append(
                WikiHit(
                    slug=slug,
                    title=title,
                    path=str(path),
                    score=value,
                    summary=summaries.get(slug, ""),
                    query_sha256=qsha,
                )
            )
        hits.sort(key=lambda h: (-h.score, h.slug))
        return hits[:limit]

    async def read_page(self, slug: str) -> str | None:
        """Read one page's markdown by slug.

        Args:
            slug: Page slug (filename stem).

        Returns:
            str | None: Page text, or None when absent.
        """
        path = self._pages().get(slug)
        return path.read_text(encoding="utf-8") if path is not None else None

    # -- ingest ---------------------------------------------------------------
    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """File one rendered article into the wiki.

        Args:
            thread_id: Run thread identifier (page provenance).
            query: Original research query (title + slug source).
            article_path: Rendered ``article.md`` path.

        Returns:
            IngestResult: Verdict (new/update/no_material) + write counts.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        self._ensure_layout()
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"wiki ingest: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        digest = common.sha256_text(text)
        title = common.page_title(query)
        slug = common.slugify(query)
        page = self.root / "sources" / f"{slug}.md"

        cache = common.load_json(self.root / common.CACHE_NAME)
        prior = cache.get(thread_id)
        if isinstance(prior, dict) and prior.get("sha") == digest:
            # No-material: keep everything, log it, stop (Karpathy verdict).
            self._append_log("ingest", title)
            return IngestResult(
                verdict=WikiVerdict.NO_MATERIAL,
                page_path=str(prior.get("page") or page),
                pages_touched=0,
                content_sha256=digest,
            )

        verdict = WikiVerdict.UPDATE if page.exists() else WikiVerdict.NEW
        related = self._related_slugs(slug, title, text)
        fm = {
            "type": "source",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "sha256": digest,
            "query_sha256": common.sha256_text(query),
            "tldr": common.tldr(text),
        }
        body = [f"## Article\n\n{text.rstrip()}", f"Filed from run `{thread_id}`."]
        if related:
            body.append("\n## Related\n" + "\n".join(f"- [[{r}]]" for r in related))
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + "\n\n".join(body) + "\n"
        page.write_text(rendered, encoding="utf-8")

        touched = 1  # the page
        touched += self._upsert_index(slug, title, fm["tldr"])
        self._append_log("ingest", title)
        touched += 1  # the log
        self._update_overview()
        touched += 1  # the overview
        cache[thread_id] = {"sha": digest, "page": str(page)}
        common.write_json(self.root / common.CACHE_NAME, cache)
        return IngestResult(verdict=verdict, page_path=str(page), pages_touched=touched, content_sha256=digest)

    # -- maintenance ----------------------------------------------------------
    def _index_summaries(self) -> dict[str, str]:
        """Parse ``index.md`` entries into slug -> one-line summary.

        Returns:
            dict[str, str]: Slug -> summary text.
        """
        index = self.root / common.INDEX_NAME
        if not index.is_file():
            return {}
        summaries: dict[str, str] = {}
        pattern = re.compile(r"^- \[[^\]]+\]\(\S*?/?([^/)]+\.md)\)(?: — (.+))?$")
        for line in index.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line.strip())
            if match is not None:
                summaries.setdefault(match.group(1)[: -len(".md")], match.group(2) or "")
        return summaries

    def _upsert_index(self, slug: str, title: str, summary: str) -> int:
        """Insert or replace the page's index entry.

        Args:
            slug: Page slug.
            title: Page title.
            summary: One-line summary.

        Returns:
            int: 1 when the index changed, 0 when the entry was identical.
        """
        index = self.root / common.INDEX_NAME
        lines = index.read_text(encoding="utf-8").splitlines()
        entry = f"- [{title}](sources/{slug}.md) — {summary}"
        prefix = f"](sources/{slug}.md)"
        changed = False
        replaced = False
        out: list[str] = []
        for line in lines:
            if prefix in line and line.startswith("- ["):
                if not replaced:
                    out.append(entry)
                    changed = changed or line != entry
                    replaced = True
                else:
                    changed = True  # duplicate entry removed
                continue
            out.append(line)
        if not replaced:
            out.append(entry)
            changed = True
        if changed:
            index.write_text("\n".join(out) + "\n", encoding="utf-8")
        return 1 if changed else 0

    def _append_log(self, op: str, title: str) -> None:
        """Append one entry to ``log.md``.

        Args:
            op: Operation name.
            title: Page title.
        """
        log = self.root / common.LOG_NAME
        with log.open("a", encoding="utf-8") as fh:
            fh.write(common.log_line(op, title) + "\n")

    def _update_overview(self) -> None:
        """Regenerate ``overview.md`` from the current page set.

        The overview is derived, never hand-maintained: one line per source
        page with its title and date, so it cannot drift from the pages.
        """
        entries: list[str] = []
        for slug, path in sorted(self._pages().items()):
            fm, _ = common.split_frontmatter(path.read_text(encoding="utf-8"))
            title = common.fm_title(fm, path)
            date = str(fm.get("date") or "")
            entries.append(f"- [{title}](sources/{slug}.md) {date}".rstrip())
        overview = self.root / common.OVERVIEW_NAME
        overview.write_text("# Overview\n\n" + "\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")

    def _related_slugs(self, slug: str, title: str, text: str) -> list[str]:
        """Existing pages sharing >=2 terms with the new page (one-way links).

        Args:
            slug: The new page's slug (excluded from its own related list).
            title: The new page's title.
            text: The new page's text.

        Returns:
            list[str]: Up to ``_RELATED_MAX`` related slugs, best first.
        """
        page_terms = set(common.terms(f"{title} {text}"))
        if not page_terms:
            return []
        scored: list[tuple[float, str]] = []
        for other, path in self._pages().items():
            if other == slug:
                continue
            ofm, obody = common.split_frontmatter(path.read_text(encoding="utf-8"))
            other_terms = set(common.terms(f"{common.fm_title(ofm, path)} {obody}"))
            overlap = len(page_terms & other_terms)
            if overlap >= _RELATED_TERMS:
                scored.append((-float(overlap), other))
        scored.sort()
        return [s for _, s in scored[:_RELATED_MAX]]


__all__ = ["FileWikiStore"]

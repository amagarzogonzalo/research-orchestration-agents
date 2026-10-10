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

import logging
import re
from pathlib import Path

import yaml

from knowledge_engine.core.exceptions import KnowledgeEngineError
from knowledge_engine.core.ports import WikiIngestPort, WikiQueryPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.sidecar import WikiSidecar
from knowledge_engine.schemas.wiki import IngestResult, WikiExtraction, WikiHit, WikiThesis, WikiVerdict

logger = logging.getLogger(__name__)

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
        sidecar = WikiSidecar(self.root)
        if sidecar.db_path.exists() and sidecar.fts_supported():
            # Loop 4 scale path: BM25 over the FTS5 sidecar when built.
            rows = sidecar.search(query, limit=limit)
            summaries = self._index_summaries()
            side_hits: list[WikiHit] = []
            for _namespaced, path, score in rows:
                p = Path(path)
                if not p.is_file():
                    continue
                text = p.read_text(encoding="utf-8")
                fm, _body = common.split_frontmatter(text)
                qsha = str(fm.get("query_sha256") or "")
                side_hits.append(
                    WikiHit(
                        slug=p.stem,
                        title=common.fm_title(fm, p),
                        path=str(p),
                        score=-float(score),
                        summary=summaries.get(p.stem, ""),
                        query_sha256=qsha,
                    )
                )
            return side_hits
        summaries = self._index_summaries()
        hits: list[WikiHit] = []
        for slug, page_path in self._pages().items():
            text = page_path.read_text(encoding="utf-8")
            fm, body = common.split_frontmatter(text)
            title = common.fm_title(fm, page_path)
            value = common.score(query_terms, title, body)
            if value <= 0.0:
                continue
            qsha = str(fm.get("query_sha256") or "")
            hits.append(
                WikiHit(
                    slug=slug,
                    title=title,
                    path=str(page_path),
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
        for other_thread, record in cache.items():
            # Cross-thread dedup (measured: duplication is cross-thread, not
            # intra-run): identical content already compiled -> link, don't
            # duplicate (close-never-delete, docs/LLM_WIKI.md Loop 2).
            if isinstance(record, dict) and record.get("sha") == digest and other_thread != thread_id:
                self._append_log("ingest", f"{title} (duplicate of {other_thread})")
                return IngestResult(
                    verdict=WikiVerdict.NO_MATERIAL,
                    page_path=str(record.get("page") or page),
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
            "confidence": "EXTRACTED",
            "tldr": common.tldr(text),
        }
        body = [f"## Article\n\n{text.rstrip()}", f"Filed from run `{thread_id}`."]
        if related:
            body.append("\n## Related\n" + "\n".join(f"- [[{r}]]" for r in related))
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + "\n\n".join(body) + "\n"
        page.write_text(rendered, encoding="utf-8")

        # Loop 4: keep the FTS5 sidecar in sync when one is maintained here.
        try:
            sidecar = WikiSidecar(self.root)
            if sidecar.db_path.exists():
                sidecar.insert_page("sources", page)
        except Exception as exc:  # noqa: BLE001 - sidecar is an accelerator, never a dependency
            logger.warning("wiki sidecar update failed (%s); continuing", exc)

        touched = 1  # the page
        touched += self._upsert_index(slug, title, fm["tldr"])
        self._append_log("ingest", title)
        touched += 1  # the log
        self._update_overview()
        touched += 1  # the overview
        cache[thread_id] = {"sha": digest, "page": str(page)}
        common.write_json(self.root / common.CACHE_NAME, cache)
        return IngestResult(verdict=verdict, page_path=str(page), pages_touched=touched, content_sha256=digest)

    async def file_answer(self, thread_id: str, query: str, article_path: str) -> str:
        """File a rendered answer under ``queries/`` (answers stay recoverable).

        Args:
            thread_id: Run thread identifier (lineage).
            query: Original research query.
            article_path: Rendered ``article.md`` path.

        Returns:
            str: The filed query page locator.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        self._ensure_layout()
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"wiki file_answer: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        title = common.page_title(query)
        slug = common.slugify(query)
        page = self.root / "queries" / f"{slug}.md"
        fm = {
            "type": "query",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "confidence": "EXTRACTED",
            "tldr": common.tldr(text),
        }
        page.parent.mkdir(parents=True, exist_ok=True)
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + f"## Answer\n\n{text.rstrip()}\n"
        page.write_text(rendered, encoding="utf-8")
        self._upsert_synthesis_index(slug, title, fm["tldr"], section="Queries", subdir="queries")
        self._append_log("query", title)
        return str(page)

    # -- compile (LLM extraction, Loop 1) -------------------------------------
    async def compile_extraction(
        self, thread_id: str, query: str, extraction: WikiExtraction, feedback: list[str]
    ) -> list[str]:
        """File concept/entity pages as reviewable drafts (never live pages).

        Args:
            thread_id: Run thread identifier (lineage).
            query: Original research query (context for summaries).
            extraction: Validated extraction payload.
            feedback: Rejection feedback lines addressed in each draft body.

        Returns:
            list[str]: Draft page locators.
        """
        self._ensure_layout()
        drafts: list[str] = []
        source_slug = common.slugify(query)
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
            fm = {
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
            text = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body_text
            path = self.root / "drafts" / f"{slug}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            drafts.append(str(path))
        self._append_log("compile", f"{len(drafts)} drafts | {common.page_title(query)}")
        return drafts

    async def file_thesis(self, thread_id: str, thesis: WikiThesis) -> str:
        """File a thesis verdict page into ``synthesis/`` (published).

        Args:
            thread_id: Run thread identifier (lineage).
            thesis: Validated thesis payload.

        Returns:
            str: The synthesis page locator.
        """
        self._ensure_layout()
        slug = common.slugify(thesis.claim)
        fm = {
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
        page = self.root / "synthesis" / f"{slug}.md"
        page.write_text(
            "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
        self._upsert_synthesis_index(slug, fm["title"], fm["tldr"])
        self._append_log("thesis", fm["title"])
        return str(page)

    async def feedback_for(self, query: str) -> list[str]:
        """Return rejection feedback recorded against this query's pages.

        Args:
            query: Research query whose slug pages may have feedback.

        Returns:
            list[str]: Feedback lines (empty when none).
        """
        records = common.load_json(self.root / common.FEEDBACK_NAME)
        entry = records.get(common.slugify(query))
        if isinstance(entry, list):
            return [str(line) for line in entry]
        return []

    # -- draft review API (human/agent; not part of the graph) ----------------
    def approve_draft(self, slug: str) -> str:
        """Publish one draft page after its hand-edit hash guard passes.

        Args:
            slug: Draft slug (``.drafts/<slug>.md``).

        Returns:
            str: The published page locator.

        Raises:
            KnowledgeEngineError: When the draft is missing or was hand-edited
                after filing (hash mismatch — fail-closed, never overwrite).
        """
        draft = self.root / "drafts" / f"{slug}.md"
        if not draft.is_file():
            raise KnowledgeEngineError(f"wiki review: draft missing: {slug}")
        text = draft.read_text(encoding="utf-8")
        fm, body = common.split_frontmatter(text)
        recorded = str(fm.get("sha256") or "")
        if recorded and recorded != common.sha256_text(body):
            raise KnowledgeEngineError(f"wiki review: hand-edited draft refuses approval (hash mismatch): {slug}")
        fm.pop("status", None)
        fm["date"] = common.today()
        kind = str(fm.get("type", "concept"))
        target_dir = "entities" if kind == "entity" else "concepts"
        page = self.root / target_dir / f"{slug}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body, encoding="utf-8")
        draft.unlink()
        self._upsert_index(slug, str(fm.get("title", slug)), str(fm.get("tldr", "")), subdir=target_dir)
        self._update_overview()
        self._append_log("publish", str(fm.get("title", slug)))
        return str(page)

    def reject_draft(self, slug: str, feedback: str, *, query: str | None = None) -> None:
        """Reject one draft, recording the feedback for the next compile.

        Args:
            slug: Draft slug.
            feedback: What the next compile must address.
            query: The originating research query (feedback is keyed by its
                slug so :meth:`feedback_for` finds it); falls back to the
                draft slug when omitted.
        """
        draft = self.root / "drafts" / f"{slug}.md"
        if draft.is_file():
            draft.unlink()
        records = common.load_json(self.root / common.FEEDBACK_NAME)
        key = common.slugify(query) if query else slug
        lines = [str(line) for line in records.get(key, []) if isinstance(line, str)]
        if feedback not in lines:
            lines.append(feedback)
        records[key] = lines
        common.write_json(self.root / common.FEEDBACK_NAME, records)
        self._append_log("reject", f"{slug} | {feedback[:80]}")

    def flag_contradiction(self, slug: str, claim: str) -> str | None:
        """Flag a contradiction on a live page (DISPUTED; close-never-delete).

        Appends the claim under a ``## Contradictions`` section and returns
        the page locator, without ever deleting anything (the earlier claim
        stays retrievable). Pages report as disputed via
        :meth:`disputed_slugs`.

        Args:
            slug: Live page slug.
            claim: The contradicting claim to record.

        Returns:
            str | None: The flagged page locator, or None when the page is
            missing.
        """
        path = self._pages().get(slug)
        if path is None:
            return None
        text = path.read_text(encoding="utf-8")
        fm, body = common.split_frontmatter(text)
        if "## Contradictions" not in body:
            body = body.rstrip() + "\n\n## Contradictions\n"
        marker = "## Contradictions\n"
        head, _, tail = body.partition(marker)
        tail_lines = tail.splitlines()
        if not any(claim in line for line in tail_lines):
            tail = "\n".join([f"- {claim}"] + [line for line in tail_lines if line.strip()]) + "\n"
        fm["status"] = str(WikiVerdict.DISPUTED.value)
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + head + marker + tail
        path.write_text(rendered, encoding="utf-8")
        self._append_log("disputed", f"{slug} | {claim[:80]}")
        return str(path)

    def disputed_slugs(self) -> list[str]:
        """Return slugs whose pages carry the disputed flag.

        Returns:
            list[str]: Disputed slugs, sorted.
        """
        disputed: list[str] = []
        for slug, path in self._pages().items():
            fm, _ = common.split_frontmatter(path.read_text(encoding="utf-8"))
            if str(fm.get("status") or "") == WikiVerdict.DISPUTED.value:
                disputed.append(slug)
        return sorted(disputed)

    def _upsert_synthesis_index(
        self, slug: str, title: str, summary: str, *, section: str = "Synthesis", subdir: str = "synthesis"
    ) -> None:
        """Add an entry under the index's named section (Synthesis/Queries).

        Args:
            slug: Page slug.
            title: Page title.
            summary: One-line summary.
            section: Index section heading.
            subdir: Page subdirectory in the link.
        """
        index = self.root / common.INDEX_NAME
        text = index.read_text(encoding="utf-8")
        marker = f"## {section}\n"
        if marker not in text:
            text = text.rstrip() + "\n\n" + marker
        head, _, tail = text.partition(marker)
        entry = f"- [{title}]({subdir}/{slug}.md) — {summary}"
        tail_lines = tail.splitlines()
        if not any(f"]({subdir}/{slug}.md)" in line for line in tail_lines):
            tail = "\n".join([entry] + [line for line in tail_lines if line.strip()]) + "\n"
        index.write_text(head + marker + tail, encoding="utf-8")

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

    def _upsert_index(self, slug: str, title: str, summary: str, *, subdir: str = "sources") -> int:
        """Insert or replace the page's index entry.

        Args:
            slug: Page slug.
            title: Page title.
            summary: One-line summary.
            subdir: Page subdirectory (``sources``/``concepts``/``entities``).

        Returns:
            int: 1 when the index changed, 0 when the entry was identical.
        """
        index = self.root / common.INDEX_NAME
        lines = index.read_text(encoding="utf-8").splitlines()
        entry = f"- [{title}]({subdir}/{slug}.md) — {summary}"
        prefix = f"]({subdir}/{slug}.md)"
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

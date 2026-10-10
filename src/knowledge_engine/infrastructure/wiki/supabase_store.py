"""SupabaseWikiStore — cloud wiki adapter, no-op-until-configured (ADR wiki-loop0).

Pages live in the ``wiki`` Storage bucket under ``pages/<slug>.md``;
``index.md``/``log.md``/``overview.md`` are bucket-root objects. Requires the
``supabase`` extra (opt-in per AGENTS rule 4) and ``SUPABASE_URL`` +
``SUPABASE_SECRET_KEY``; the engine only builds this adapter when
``wiki_store_kind='supabase'``, so the default local path is untouched.

The SHA ingest cache is in-process (per construction) — documented trade-off:
cloud cache coherence across processes lands in Loop 3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from knowledge_engine.core.exceptions import ConfigurationError, KnowledgeEngineError
from knowledge_engine.core.ports import VaultLintPort, WikiIngestPort, WikiQueryPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.lint_core import lint_pages
from knowledge_engine.schemas.wiki import IngestResult, LintReport, WikiExtraction, WikiHit, WikiThesis, WikiVerdict

_PAGES_PREFIX = "pages"


class SupabaseWikiStore(WikiQueryPort, WikiIngestPort):
    """Supabase Storage-backed wiki (cross-machine safe when configured).

    Args:
        client: Supabase client (from :meth:`from_env`).
        bucket: Storage bucket name (created on demand as PUBLIC).
    """

    def __init__(self, client: Any, bucket: str = "wiki") -> None:
        self._client = client
        self._bucket = bucket
        self._cache: dict[str, dict[str, str]] = {}

    @classmethod
    def from_env(cls) -> SupabaseWikiStore:
        """Build the store from ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``.

        Returns:
            SupabaseWikiStore: Configured store.

        Raises:
            ConfigurationError: When the keys or the ``supabase`` extra are
                missing (the engine treats this as no-op-until-configured).
        """
        import os

        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SECRET_KEY", "")
        if not url or not key:
            raise ConfigurationError("wiki_store_kind='supabase' needs SUPABASE_URL and SUPABASE_SECRET_KEY")
        try:
            from supabase import create_client
        except ImportError as exc:
            raise ConfigurationError(
                "the supabase wiki adapter needs the 'supabase' extra: uv sync --extra supabase"
            ) from exc
        return cls(create_client(url, key))

    def _from(self) -> Any:
        """Return the bucket handle, creating the bucket when missing.

        Returns:
            Any: Supabase Storage bucket handle.

        Raises:
            KnowledgeEngineError: When the bucket cannot be created.
        """
        storage = self._client.storage
        try:
            existing = {b.name for b in storage.list_buckets()}
        except Exception as exc:  # noqa: BLE001 - cloud listing failure is a hard stop
            raise KnowledgeEngineError(f"supabase wiki: cannot list buckets: {exc}") from exc
        if self._bucket not in existing:
            try:
                storage.create_bucket(self._bucket, options={"public": False})
            except Exception:  # noqa: BLE001 - duplicate-bucket races are benign
                pass
        return storage.from_(self._bucket)

    def _download(self, handle: Any, key: str) -> str | None:
        """Download one object's text, or None when absent.

        Args:
            handle: Bucket handle.
            key: Object key.

        Returns:
            str | None: Object text or None.
        """
        try:
            blob = handle.download(key)
        except Exception:  # noqa: BLE001 - absent objects are a normal miss
            return None
        return blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)

    async def search_wiki(self, query: str, *, limit: int = 3) -> list[WikiHit]:
        """Return the best-matching wiki pages for a query.

        Args:
            query: Research query string.
            limit: Maximum hits to return.

        Returns:
            list[WikiHit]: Ranked hits (empty when nothing matches or the
            bucket is unreachable).
        """
        query_terms = common.terms(query)
        if not query_terms:
            return []
        handle = self._from()
        summaries = self._index_summaries(handle)
        hits: list[WikiHit] = []
        try:
            objects = handle.list(_PAGES_PREFIX) or []
        except Exception:  # noqa: BLE001 - listing failure degrades to no hits
            return []
        for obj in objects:
            name = getattr(obj, "name", None) or (obj.get("name") if isinstance(obj, dict) else None)
            if not name or not name.endswith(".md"):
                continue
            slug = name[: -len(".md")]
            text = self._download(handle, f"{_PAGES_PREFIX}/{name}")
            if text is None:
                continue
            fm, body = common.split_frontmatter(text)
            title = common.fm_title(fm, Path(name))
            value = common.score(query_terms, title, body)
            if value <= 0.0:
                continue
            loc = f"{self._bucket}/{_PAGES_PREFIX}/{name}"
            qsha = str(fm.get("query_sha256") or "")
            hits.append(
                WikiHit(
                    slug=slug,
                    title=title,
                    path=loc,
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
            slug: Page slug.

        Returns:
            str | None: Page text, or None when absent.
        """
        return self._download(self._from(), f"{_PAGES_PREFIX}/{slug}.md")

    async def ingest(self, thread_id: str, query: str, article_path: str) -> IngestResult:
        """Upload one rendered article into the wiki bucket.

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
            raise KnowledgeEngineError(f"supabase wiki ingest: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        digest = common.sha256_text(text)
        title = common.page_title(query)
        slug = common.slugify(query)
        key = f"{_PAGES_PREFIX}/{slug}.md"

        cache = self._load_cloud_cache()
        prior = cache.get(thread_id)
        if isinstance(prior, dict) and prior.get("sha") == digest:
            self._append_log(self._from(), "ingest", title)
            self._save_cloud_cache(cache)
            return IngestResult(
                verdict=WikiVerdict.NO_MATERIAL,
                page_path=str(prior.get("page") or f"{self._bucket}/{key}"),
                pages_touched=0,
                content_sha256=digest,
            )
        for other_thread, recorded in cache.items():
            if isinstance(recorded, dict) and recorded.get("sha") == digest and other_thread != thread_id:
                self._append_log(self._from(), "ingest", f"{title} (duplicate of {other_thread})")
                self._save_cloud_cache(cache)
                return IngestResult(
                    verdict=WikiVerdict.NO_MATERIAL,
                    page_path=str(recorded.get("page") or f"{self._bucket}/{key}"),
                    pages_touched=0,
                    content_sha256=digest,
                )

        existing = self._download(self._from(), key)
        verdict = WikiVerdict.UPDATE if existing is not None else WikiVerdict.NEW
        fm = {
            "type": "source",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "sha256": digest,
            "query_sha256": common.sha256_text(query),
            "tldr": common.tldr(text),
        }
        trailer = f" Filed from run `{thread_id}`.\n"
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n## Article\n\n" + text.rstrip() + trailer
        self._from().upload(key, rendered.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"})
        touched = 1
        touched += self._upsert_index(self._from(), slug, title, fm["tldr"])
        self._append_log(self._from(), "ingest", title)
        touched += 1
        loc = f"{self._bucket}/{key}"
        cache[thread_id] = {"sha": digest, "page": loc}
        self._save_cloud_cache(cache)
        return IngestResult(verdict=verdict, page_path=loc, pages_touched=touched, content_sha256=digest)

    # -- compile (LLM extraction, Loop 1) -------------------------------------
    async def compile_extraction(
        self, thread_id: str, query: str, extraction: WikiExtraction, feedback: list[str]
    ) -> list[str]:
        """Upload concept/entity drafts into the bucket's ``drafts/`` area.

        Args:
            thread_id: Run thread identifier (lineage).
            query: Original research query (context for summaries).
            extraction: Validated extraction payload.
            feedback: Rejection feedback lines addressed in each draft body.

        Returns:
            list[str]: Draft object locators.
        """
        handle = self._from()
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
            key = f"drafts/{slug}.md"
            rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body_text
            handle.upload(key, rendered.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"})
            locs.append(f"{self._bucket}/{key}")
        self._append_log(handle, "compile", f"{len(locs)} drafts | {common.page_title(query)}")
        return locs

    async def file_thesis(self, thread_id: str, thesis: WikiThesis) -> str:
        """Upload a thesis verdict page into the bucket's ``synthesis/`` area.

        Args:
            thread_id: Run thread identifier (lineage).
            thesis: Validated thesis payload.

        Returns:
            str: The synthesis object locator.
        """
        handle = self._from()
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
        key = f"synthesis/{slug}.md"
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + "\n".join(lines) + "\n"
        handle.upload(key, rendered.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"})
        self._append_log(handle, "thesis", fm["title"])
        return f"{self._bucket}/{key}"

    async def file_answer(self, thread_id: str, query: str, article_path: str) -> str:
        """Upload a rendered answer under the bucket's ``queries/`` area.

        Args:
            thread_id: Run thread identifier (lineage).
            query: Original research query.
            article_path: Rendered ``article.md`` path.

        Returns:
            str: The filed query object locator.

        Raises:
            KnowledgeEngineError: When the article file is missing.
        """
        src = Path(article_path)
        if not src.is_file():
            raise KnowledgeEngineError(f"supabase wiki file_answer: article missing: {article_path}")
        text = src.read_text(encoding="utf-8")
        title = common.page_title(query)
        slug = common.slugify(query)
        handle = self._from()
        fm = {
            "type": "query",
            "title": title,
            "thread": thread_id,
            "date": common.today(),
            "confidence": "EXTRACTED",
            "tldr": common.tldr(text),
        }
        key = f"queries/{slug}.md"
        rendered = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + f"## Answer\n\n{text.rstrip()}\n"
        handle.upload(key, rendered.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"})
        self._append_log(handle, "query", title)
        return f"{self._bucket}/{key}"

    async def feedback_for(self, query: str) -> list[str]:
        """Return rejection feedback stored as the bucket ``.feedback.json``.

        Args:
            query: Research query whose slug pages may have feedback.

        Returns:
            list[str]: Feedback lines (empty when none).
        """
        handle = self._from()
        text = self._download(handle, common.FEEDBACK_NAME)
        if text is None:
            return []
        try:
            records = json.loads(text)
        except json.JSONDecodeError:
            return []
        entry = records.get(common.slugify(query)) if isinstance(records, dict) else None
        return [str(line) for line in entry] if isinstance(entry, list) else []

    # -- bucket object helpers ----------------------------------------------
    def _load_cloud_cache(self) -> dict[str, dict[str, str]]:
        """Load the shared cache object (cross-process coherence, Loop 3).

        Merges the bucket ``.cache.json`` with the in-process copy so
        concurrent processes see each other's filings on the next ingest.

        Returns:
            dict[str, dict[str, str]]: Merged cache mapping thread -> {sha, page}.
        """
        handle = self._from()
        text = self._download(handle, common.CACHE_NAME)
        if text is None:
            return dict(self._cache)
        try:
            cloud = json.loads(text)
        except json.JSONDecodeError:
            return dict(self._cache)
        merged = dict(self._cache)
        if isinstance(cloud, dict):
            merged.update(cloud)
        return merged

    def _save_cloud_cache(self, cache: dict[str, dict[str, str]]) -> None:
        """Persist the cache as the shared bucket object.

        Args:
            cache: The merged cache mapping.
        """
        self._from().upload(
            common.CACHE_NAME,
            json.dumps(cache, indent=2, ensure_ascii=False).encode("utf-8"),
            {"content-type": "application/json", "upsert": "true"},
        )
        self._cache = dict(cache)

    def _lint_bucket(self) -> dict[str, str]:
        """Shared cloud lint: enumerate drafts + pages, return raw page maps.

        Returns:
            dict[str, str]: locator -> page text for drafts and filed pages.
        """
        handle = self._from()
        pages: dict[str, str] = {}
        for prefix in ("drafts", "synthesis", "concepts", "entities", "pages", "queries"):
            try:
                objects = handle.list(prefix) or []
            except Exception:  # noqa: BLE001 - listing failure skips this area
                continue
            for obj in objects:
                name = getattr(obj, "name", None) or (obj.get("name") if isinstance(obj, dict) else None)
                if not name or not name.endswith(".md"):
                    continue
                text = self._download(handle, f"{prefix}/{name}")
                if text is not None:
                    pages[f"{prefix}/{name[: -len('.md')]}"] = text
        return pages

    def _index_summaries(self, handle: Any) -> dict[str, str]:
        """Parse the bucket ``index.md`` into slug -> summary.

        Args:
            handle: Bucket handle.

        Returns:
            dict[str, str]: Slug -> summary text.
        """
        text = self._download(handle, common.INDEX_NAME)
        if text is None:
            return {}
        summaries: dict[str, str] = {}
        import re

        pattern = re.compile(r"^- \[[^\]]+\]\(\S*?/?([^/)]+\.md)\)(?: — (.+))?$")
        for line in text.splitlines():
            match = pattern.match(line.strip())
            if match is not None:
                summaries.setdefault(match.group(1)[: -len(".md")], match.group(2) or "")
        return summaries

    def _upsert_index(self, handle: Any, slug: str, title: str, summary: str) -> int:
        """Insert or replace the page's index entry in the bucket.

        Args:
            handle: Bucket handle.
            slug: Page slug.
            title: Page title.
            summary: One-line summary.

        Returns:
            int: 1 when the index changed, 0 when identical.
        """
        text = self._download(handle, common.INDEX_NAME) or "# Knowledge Base Index\n\n## Sources\n"
        lines = text.splitlines()
        entry = f"- [{title}]({_PAGES_PREFIX}/{slug}.md) — {summary}"
        prefix = f"]({_PAGES_PREFIX}/{slug}.md)"
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
                    changed = True
                continue
            out.append(line)
        if not replaced:
            out.append(entry)
            changed = True
        if changed:
            handle.upload(
                common.INDEX_NAME,
                ("\n".join(out) + "\n").encode("utf-8"),
                {"content-type": "text/markdown", "upsert": "true"},
            )
        return 1 if changed else 0

    def _append_log(self, handle: Any, op: str, title: str) -> None:
        """Append one entry to the bucket ``log.md``.

        Args:
            handle: Bucket handle.
            op: Operation name.
            title: Page title.
        """
        text = self._download(handle, common.LOG_NAME) or "# Wiki Log\n"
        updated = text.rstrip() + "\n" + common.log_line(op, title) + "\n"
        handle.upload(common.LOG_NAME, updated.encode("utf-8"), {"content-type": "text/markdown", "upsert": "true"})


class SupabaseVaultLinter(VaultLintPort):
    """Scan a Supabase wiki bucket and report structural issues (Loop 3)."""

    def __init__(self, store: SupabaseWikiStore) -> None:
        """Create a SupabaseVaultLinter.

        Args:
            store: A :class:`SupabaseWikiStore` (shared bucket handle +
                error policy; one client, one bucket).
        """
        self._store = store

    async def lint(self) -> LintReport:
        """Scan the bucket's page areas and report findings.

        Returns:
            LintReport: Findings; ``ok`` is True when the bucket is clean.
        """
        pages = self._store._lint_bucket()
        index_text = self._store._download(self._store._from(), common.INDEX_NAME)
        return lint_pages(pages, root=str(self._store._bucket), index_text=index_text)


__all__ = ["SupabaseVaultLinter", "SupabaseWikiStore"]

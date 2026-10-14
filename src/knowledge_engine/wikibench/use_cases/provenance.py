"""Use case: keep the wiki healthy — provenance invariant.

Suite ``provenance`` files a few articles and asserts the wiki's trust contract:
every ``[[wikilink]]`` resolves to a real page, and every page carries its
provenance frontmatter (``thread`` + ``date``). It is the deterministic proxy for
the wiki pattern's main safeguard — citations must trace back to a source.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.file_store import FileWikiStore
from knowledge_engine.infrastructure.wiki.lint import FileVaultLinter
from knowledge_engine.wikibench.schemas import SuiteResult
from knowledge_engine.wikibench.use_cases._common import write_article

_USE_CASE = "maintain-wiki"
_PAD = "Padding sentence keeps the page above the thin threshold. "
_ARTICLE_A = "# Alpha topic\n\nAlpha topic overview. " + _PAD * 20
_ARTICLE_B = "# Alpha topic two\n\nAlpha topic two builds on alpha topic. " + _PAD * 20
_ANSWER = "# Alpha answer\n\nA filed answer about alpha topic. " + _PAD * 20


def _page_files(root: Path) -> list[Path]:
    """Return every live page file under the wiki root.

    Args:
        root: Wiki root.

    Returns:
        list[Path]: Page paths across the page directories.
    """
    files: list[Path] = []
    for name in common.PAGE_DIRS:
        directory = root / name
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.md")))
    return files


async def provenance(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run the provenance-invariant suite.

    Args:
        workspace: Scratch directory for the wiki and its articles.
        scale: Corpus size (unused; the suite files fixed articles).
        seed: Corpus seed (unused).
        backend: Backend id.

    Returns:
        SuiteResult: Provenance metrics and threshold findings.
    """
    store = FileWikiStore(workspace / "wiki")
    articles = workspace / "articles"
    await store.ingest("t1", "alpha topic", write_article(articles / "a.md", _ARTICLE_A))
    await store.ingest("t2", "alpha topic two", write_article(articles / "b.md", _ARTICLE_B))
    await store.file_answer("t3", "alpha topic", write_article(articles / "ans.md", _ANSWER))

    pages = _page_files(store.root)
    missing = 0
    for page in pages:
        fm, _ = common.split_frontmatter(page.read_text(encoding="utf-8"))
        if not fm.get("thread") or not fm.get("date"):
            missing += 1
    broken = len((await FileVaultLinter(store.root).lint()).broken_links)

    measured = {
        "pages": float(len(pages)),
        "broken_links": float(broken),
        "missing_provenance": float(missing),
        "ok": 1.0 if broken == 0 and missing == 0 else 0.0,
    }
    findings: list[str] = []
    if broken:
        findings.append(f"{broken} broken wikilink(s)")
    if missing:
        findings.append(f"{missing} page(s) missing thread/date provenance")
    return SuiteResult(
        name="provenance",
        use_case=_USE_CASE,
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["provenance"]

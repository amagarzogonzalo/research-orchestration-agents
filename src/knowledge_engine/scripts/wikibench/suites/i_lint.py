"""Suite I — maintenance: does the linter find its own defects?

Each deterministic defect class is injected into an otherwise clean vault and
must be reported (recall 1.0), without spurious findings (precision 1.0); a
clean vault must produce zero findings.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.lint import FileVaultLinter
from knowledge_engine.scripts.wikibench.corpus import build_corpus, seed_vault
from knowledge_engine.scripts.wikibench.metrics import mean
from knowledge_engine.scripts.wikibench.schemas import SuiteResult

_CLASSES = ("missing_frontmatter", "broken_links", "thin_pages", "index_drift")
_PAD = "Padding sentence keeps this page well above the thin threshold. "


def _first_page(root: Path) -> Path:
    """Return the first seeded source page.

    Args:
        root: Wiki root.

    Returns:
        Path: First page under ``sources/``.
    """
    return sorted((root / "sources").glob("*.md"))[0]


def _inject_missing_frontmatter(root: Path) -> None:
    """Strip the frontmatter block from one page.

    Args:
        root: Wiki root.
    """
    page = _first_page(root)
    _, body = common.split_frontmatter(page.read_text(encoding="utf-8"))
    page.write_text(body, encoding="utf-8")


def _inject_broken_link(root: Path) -> None:
    """Add a ``[[wikilink]]`` that points nowhere.

    Args:
        root: Wiki root.
    """
    page = _first_page(root)
    page.write_text(page.read_text(encoding="utf-8") + "\n\nSee [[ghost-page-xyz]] for more.\n", encoding="utf-8")


def _inject_thin_page(root: Path) -> None:
    """Add a valid but too-short page (and index it, so only thin fires).

    Args:
        root: Wiki root.
    """
    fm = {"type": "source", "title": "Thin Extra", "date": "2026-01-01"}
    page = root / "sources" / "thin-extra.md"
    page.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\nToo short.\n", encoding="utf-8")
    index = root / common.INDEX_NAME
    index.write_text(index.read_text(encoding="utf-8") + "- [Thin Extra](sources/thin-extra.md)\n", encoding="utf-8")


def _inject_index_drift(root: Path) -> None:
    """Add a well-formed page that is never indexed (drift + orphan).

    Args:
        root: Wiki root.
    """
    fm = {"type": "source", "title": "Drift Extra", "date": "2026-01-01"}
    page = root / "sources" / "drift-extra.md"
    page.write_text(
        "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\nDrift body. " + _PAD * 4,
        encoding="utf-8",
    )


_INJECTORS = {
    "missing_frontmatter": _inject_missing_frontmatter,
    "broken_links": _inject_broken_link,
    "thin_pages": _inject_thin_page,
    "index_drift": _inject_index_drift,
}

_DETECTABLE = ("missing_frontmatter", "broken_links", "thin_pages", "index_drift", "orphans")


async def run(*, workspace: Path, scale: int, seed: int, backend: str) -> SuiteResult:
    """Run suite I and return its lint-detection metrics.

    Args:
        workspace: Scratch directory for the injected vaults.
        scale: Corpus size used for the clean baseline.
        seed: Corpus seed.
        backend: Backend id.

    Returns:
        SuiteResult: Lint precision/recall metrics and threshold findings.
    """
    built = build_corpus(scale, seed)
    recall_hits = 0
    precisions: list[float] = []
    for name, inject in _INJECTORS.items():
        root = workspace / f"i_{name}"
        seed_vault(root, built)
        inject(root)
        report = await FileVaultLinter(root).lint()
        detected = {field for field in _DETECTABLE if getattr(report, field)}
        expected = {name} | ({"orphans"} if name == "index_drift" else set())
        recall_hits += int(name in detected)
        precisions.append(len(detected & expected) / len(detected) if detected else 0.0)

    clean_root = workspace / "i_clean"
    seed_vault(clean_root, built)
    clean = await FileVaultLinter(clean_root).lint()

    measured = {
        "clean_ok": 1.0 if clean.ok else 0.0,
        "recall": recall_hits / len(_CLASSES),
        "precision": mean(precisions),
    }
    findings: list[str] = []
    if measured["clean_ok"] < 1.0:
        findings.append("clean vault reported findings")
    if measured["recall"] < 1.0:
        findings.append(f"lint recall {measured['recall']:.3f} < 1.0")
    if measured["precision"] < 1.0:
        findings.append(f"lint precision {measured['precision']:.3f} < 1.0")
    return SuiteResult(
        name="i_lint",
        backend=backend,
        tier="0",
        scale=scale,
        metrics=measured,
        passed=not findings,
        findings=findings,
    )


__all__ = ["run"]

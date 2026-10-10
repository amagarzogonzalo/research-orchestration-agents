"""WikiDeepLinter — LLM-assisted wiki health check (contradictions/stale/gaps).

Runs the deterministic :meth:`~knowledge_engine.infrastructure.wiki.lint_core.
lint_pages` scan unchanged, then (optionally, with a provider) asks the model
to surface what only reading across pages makes visible:

* **contradictions** — claims on different pages that tension each other;
* **stale** — claims a newer page has superseded;
* **gaps** — concepts mentioned but lacking a dedicated page.

Degrade-safe: a missing/failing provider yields an empty :class:`DeepLintReport`
plus the deterministic findings — the deep pass never blocks on the model.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from knowledge_engine.core.ports import ProviderPort, VaultLintPort
from knowledge_engine.infrastructure.wiki import common
from knowledge_engine.infrastructure.wiki.lint_core import lint_pages
from knowledge_engine.schemas.provider import Affinity, ChatRequest
from knowledge_engine.schemas.wiki import DeepLintReport, LintReport

logger = logging.getLogger(__name__)

_DEEP_LINT_SYSTEM = (
    "You are a wiki curator. Review the page abstracts below for cross-page "
    'issues. Reply ONLY with JSON: {"contradictions": [str], "stale": [str], '
    '"gaps": [str]}. Keep findings concrete; return empty lists when the wiki '
    "is healthy."
)


class WikiDeepLinter(VaultLintPort):
    """Deterministic lint + optional LLM pass over one wiki vault."""

    def __init__(self, root: str | Path, provider: ProviderPort | None = None) -> None:
        """Create a WikiDeepLinter.

        Args:
            root: Wiki root (same layout as :class:`FileWikiStore`).
            provider: Optional `ProviderPort`; when None the deep pass is
                skipped and the deterministic report is authoritative.
        """
        self.root = Path(root)
        self.provider = provider

    async def lint(self) -> LintReport:
        """Deterministic findings (structural issues only).

        Returns:
            LintReport: Structural findings.
        """
        return _scan(self.root)

    async def lint_deep(self) -> tuple[LintReport, DeepLintReport]:
        """Deterministic + LLM cross-page findings.

        Returns:
            tuple[LintReport, DeepLintReport]: Structural + model findings
            (model findings empty when no provider is configured).
        """
        structural = _scan(self.root)
        if self.provider is None:
            return structural, DeepLintReport()
        text = _digest(self.root)
        if not text.strip():
            return structural, DeepLintReport()
        payload = text[:8000]
        try:
            res = await self.provider.complete(
                ChatRequest.of(_DEEP_LINT_SYSTEM, payload, affinity=Affinity.MICRO, max_tokens=400)
            )
            data = json.loads(res.message.content)
            deep = DeepLintReport.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - deep pass degrades, never fails the lint
            logger.warning("wiki deep lint degraded (%s); deterministic report only", exc)
            deep = DeepLintReport()
        return structural, deep


def _scan(root: Path) -> LintReport:
    """Run the deterministic lint over the vault.

    Args:
        root: Wiki root directory.

    Returns:
        LintReport: Structural findings.
    """
    pages: dict[str, str] = {}
    if root.is_dir():
        for name in common.PAGE_DIRS:
            d = root / name
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                pages[p.stem] = p.read_text(encoding="utf-8")
    index_path = root / common.INDEX_NAME
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else None
    return lint_pages(pages, root=str(root), index_text=index_text)


def _digest(root: Path) -> str:
    """Build a compact cross-page digest for the model pass.

    Args:
        root: Wiki root directory.

    Returns:
        str: One line per page: title (type) — tldr/body head.
    """
    lines: list[str] = []
    if root.is_dir():
        for name in common.PAGE_DIRS:
            d = root / name
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.md")):
                fm, body = common.split_frontmatter(p.read_text(encoding="utf-8"))
                title = str(fm.get("title") or p.stem)
                head = " ".join(body.split())[:220]
                lines.append(f"- {title} ({name}): {head}")
    return "\n".join(lines)


__all__ = ["WikiDeepLinter"]

"""Unit tests: WikiDeepLinter (deterministic pass + opt-in LLM pass)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knowledge_engine.infrastructure.wiki import WikiDeepLinter


def _seed_vault(root: Path) -> None:
    """Two-page wiki fixture.

    Args:
        root: Wiki root.
    """
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "rag.md").write_text(
        "---\ntype: source\ntitle: RAG\ndate: 2026-10-09\n---\n\n"
        "Retrieval augmented generation grounds answers in retrieved documents. " * 6,
        encoding="utf-8",
    )
    (root / "sources" / "long-context.md").write_text(
        "---\ntype: source\ntitle: Long Context\ndate: 2026-10-10\n---\n\n"
        "Long context windows reduce the need for retrieval pipelines entirely. " * 6,
        encoding="utf-8",
    )


class FakeProvider:
    """Provider stub returning canned JSON for the deep-lint call.

    Attributes:
        reply: Raw JSON reply.
        calls: Number of complete() calls.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, req: Any) -> Any:
        """Return the canned reply.

        Args:
            req: Chat request.

        Returns:
            Any: ChatResult stub (message.content).
        """
        self.calls += 1
        return type("R", (), {"message": type("M", (), {"content": self.reply})()})()

    async def extract(self, req: Any) -> object:
        """Unused.

        Args:
            req: Extraction request.

        Returns:
            object: Empty payload.
        """
        return {}


async def test_deterministic_pass_without_provider(tmp_path: Path) -> None:
    """With no provider the structural report is authoritative, deep is empty."""
    _seed_vault(tmp_path)
    linter = WikiDeepLinter(tmp_path)
    structural = await linter.lint()
    assert structural.pages == 2
    structural2, deep = await linter.lint_deep()
    assert structural2.pages == 2
    assert deep.ok


async def test_llm_pass_surfaces_findings(tmp_path: Path) -> None:
    """Provider findings propagate into the DeepLintReport."""
    _seed_vault(tmp_path)
    provider = FakeProvider('{"contradictions": ["RAG vs long-context"], "stale": [], "gaps": ["memory"]}')
    linter = WikiDeepLinter(tmp_path, provider)
    _, deep = await linter.lint_deep()
    assert provider.calls == 1
    assert deep.contradictions == ["RAG vs long-context"]
    assert deep.gaps == ["memory"]
    assert not deep.ok


async def test_llm_pass_degrades_on_bad_json(tmp_path: Path) -> None:
    """Malformed provider output yields an empty deep report, never a raise."""
    _seed_vault(tmp_path)
    provider = FakeProvider("not json")
    linter = WikiDeepLinter(tmp_path, provider)
    structural, deep = await linter.lint_deep()
    assert structural.pages == 2
    assert deep.ok


async def test_empty_vault_skips_provider() -> None:
    """An empty vault never spends a model call (calls == 0)."""
    provider = FakeProvider('{"contradictions": [], "stale": [], "gaps": []}')
    linter = WikiDeepLinter(Path("does-not-exist"), provider)
    _, deep = await linter.lint_deep()
    assert provider.calls == 0
    assert deep.ok

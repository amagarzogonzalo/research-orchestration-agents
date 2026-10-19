"""Unit tests: the store-root guard for ``ResearchEngine.wiki_ingest`` paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_engine.core.exceptions import WikiArticleMissing
from knowledge_engine.engine import _resolve_article_path


def test_resolves_path_inside_the_store_root(tmp_path: Path) -> None:
    """A path under the store root is accepted and resolved."""
    target = tmp_path / "thread-1" / "article.md"
    target.parent.mkdir(parents=True)
    target.write_text("# hi", encoding="utf-8")
    resolved = _resolve_article_path(str(tmp_path), str(target))
    assert resolved == target.resolve()


def test_rejects_relative_escape(tmp_path: Path) -> None:
    """``..`` traversal out of the store root is refused."""
    with pytest.raises(WikiArticleMissing):
        _resolve_article_path(str(tmp_path), str(tmp_path / ".." / "outside.md"))


def test_rejects_absolute_path_outside_the_root(tmp_path: Path) -> None:
    """An absolute path outside the store root is refused."""
    outside = tmp_path.parent / "outside.md"
    with pytest.raises(WikiArticleMissing):
        _resolve_article_path(str(tmp_path / "store"), str(outside))


def test_rejects_traversal_even_when_target_exists(tmp_path: Path) -> None:
    """Containment is checked before the wiki store ever reads the file."""
    secret = tmp_path / "secret.md"
    secret.write_text("do not ingest", encoding="utf-8")
    root = tmp_path / "store"
    root.mkdir()
    with pytest.raises(WikiArticleMissing):
        _resolve_article_path(str(root), str(secret))

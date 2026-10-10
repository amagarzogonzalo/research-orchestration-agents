"""Contract suite: every WikiQueryPort + WikiIngestPort adapter (LSP proof).

Same behavior tests run against the fake, the local file store, and the
Supabase adapter with a fake client — any adapter passing is a drop-in
substitute (ADR wiki-loop0 TDD pyramid, layer "contract").
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeWikiStore, fake_supabase_client  # noqa: E402

from knowledge_engine.infrastructure.wiki import FileWikiStore, SupabaseWikiStore  # noqa: E402
from knowledge_engine.schemas.wiki import WikiVerdict  # noqa: E402

Adapter = FakeWikiStore | FileWikiStore | SupabaseWikiStore

FACTORIES: tuple[tuple[str, Callable[[Path], Adapter]], ...] = (
    ("fake", lambda tmp: FakeWikiStore()),
    ("file", lambda tmp: FileWikiStore(root=tmp / "wiki")),
    ("supabase", lambda tmp: SupabaseWikiStore(fake_supabase_client({}), bucket="wiki")),
)


@pytest.fixture(params=[f for f, _ in FACTORIES], ids=[name for name, _ in FACTORIES])
def adapter(request: pytest.FixtureRequest, tmp_path: Path) -> Adapter:
    """Build one adapter instance per parametrized run.

    Args:
        request: Pytest request (adapter id).
        tmp_path: Per-test temp dir.

    Returns:
        Adapter: The adapter instance under test.
    """
    factory = dict(FACTORIES)[request.param]
    return factory(tmp_path)


def _article(tmp_path: Path, text: str) -> str:
    """Write a fixture article and return its path.

    Args:
        tmp_path: Per-test temp dir.
        text: Article markdown.

    Returns:
        str: Article path.
    """
    article = tmp_path / "article.md"
    article.write_text(text, encoding="utf-8")
    return str(article)


async def test_ingest_then_search_roundtrips(adapter: Adapter, tmp_path: Path) -> None:
    """A filed page is findable by title terms on every adapter.

    Args:
        adapter: Adapter under test.
        tmp_path: Per-test temp dir.
    """
    result = await adapter.ingest(
        "t1",
        "rag hallucinations",
        _article(tmp_path, "Retrieval augmented generation reduces hallucinations in LLMs."),
    )
    assert result.verdict == WikiVerdict.NEW
    assert result.page_path is not None

    hits = await adapter.search_wiki("rag hallucinations")
    assert hits, "filed page must be findable"
    assert hits[0].slug


async def test_ingest_same_content_is_no_material(adapter: Adapter, tmp_path: Path) -> None:
    """Re-ingesting an unchanged source logs only (No-material verdict).

    Args:
        adapter: Adapter under test.
        tmp_path: Per-test temp dir.
    """
    path = _article(tmp_path, "same bytes")
    first = await adapter.ingest("t1", "topic", path)
    second = await adapter.ingest("t1", "topic", path)
    assert first.verdict == WikiVerdict.NEW
    assert second.verdict == WikiVerdict.NO_MATERIAL
    assert second.pages_touched == 0


async def test_search_miss_is_empty(adapter: Adapter) -> None:
    """An empty wiki returns no hits (the miss path stays silent).

    Args:
        adapter: Adapter under test.
    """
    assert await adapter.search_wiki("quantum tunneling") == []


async def test_read_page_missing_is_none(adapter: Adapter) -> None:
    """Reading an absent slug returns None on every adapter.

    Args:
        adapter: Adapter under test.
    """
    assert await adapter.read_page("nope") is None


async def test_cross_thread_duplicate_content_links_not_duplicates(adapter: Adapter, tmp_path: Path) -> None:
    """The same content filed under two threads compiles once (file adapter).

    Args:
        adapter: Adapter under test.
        tmp_path: Per-test temp dir.
    """
    text = "Identical research bytes across two runs."
    path = _article(tmp_path, text)
    first = await adapter.ingest("t1", "topic one", path)
    second = await adapter.ingest("t2", "topic two", path)
    assert first.verdict == WikiVerdict.NEW
    assert second.verdict == WikiVerdict.NO_MATERIAL
    assert second.pages_touched == 0
    assert second.content_sha256 == first.content_sha256

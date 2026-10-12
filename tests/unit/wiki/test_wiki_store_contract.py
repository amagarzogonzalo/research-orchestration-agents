"""Contract suite: every WikiQueryPort + WikiIngestPort adapter (LSP proof).

Same behavior tests run against the fake, the local file store, the Supabase
adapter with a fake client, and **real Postgres** (via ``WIKI_TEST_PG_DSN``,
defaulting to the local compose service). The assertions are the cross-adapter
invariant and are unchanged across all four. The Postgres param skips when no
database is reachable; each Postgres test starts from a truncated wiki schema.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeWikiStore, fake_supabase_client  # noqa: E402

from knowledge_engine.infrastructure.wiki import FileWikiStore, PostgresWikiStore, SupabaseWikiStore  # noqa: E402
from knowledge_engine.schemas.wiki import WikiVerdict  # noqa: E402

Adapter = FakeWikiStore | FileWikiStore | SupabaseWikiStore | PostgresWikiStore

_PG_DSN = os.environ.get("WIKI_TEST_PG_DSN", "postgresql://wiki:wiki@127.0.0.1:54329/wiki")


def _postgres_available() -> bool:
    """Probe the Postgres DSN once (cached) to decide skip vs run.

    Returns:
        bool: True when a connection succeeds.
    """
    try:
        import psycopg

        with psycopg.connect(_PG_DSN, connect_timeout=2) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return True
    except Exception:  # noqa: BLE001 - a probe failure just means "skip"
        return False


_HAS_PG = _postgres_available()


def _reset_postgres() -> None:
    """Truncate all wiki tables, restoring a clean state for one contract test."""
    import psycopg

    with psycopg.connect(_PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE wiki.events, wiki.revisions, wiki.queries, wiki.links, "
                "wiki.page_tags, wiki.revision_sources, wiki.claims, wiki.source_bodies, "
                "wiki.run_files, wiki.pages, wiki.runs, wiki.tags, wiki.sources, wiki.files CASCADE"
            )


def _postgres_factory(tmp: Path) -> PostgresWikiStore:
    """Build a Postgres store over the real test database.

    Args:
        tmp: Per-test temp dir (used only for articles; storage is in PG).

    Returns:
        PostgresWikiStore: Store against ``_PG_DSN``.
    """
    _reset_postgres()
    return PostgresWikiStore.from_dsn(_PG_DSN)


FACTORIES: tuple[tuple[str, Callable[[Path], Adapter]], ...] = (
    ("fake", lambda tmp: FakeWikiStore()),
    ("file", lambda tmp: FileWikiStore(root=tmp / "wiki")),
    ("supabase", lambda tmp: SupabaseWikiStore(fake_supabase_client({}), bucket="wiki")),
    ("postgres", _postgres_factory),
)

_PARAM_ONLY = pytest.param(
    "postgres",
    marks=pytest.mark.skipif(not _HAS_PG, reason="WIKI_TEST_PG_DSN not reachable"),
)


@pytest.fixture(
    params=["fake", "file", "supabase", _PARAM_ONLY],
    ids=["fake", "file", "supabase", "postgres"],
)
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

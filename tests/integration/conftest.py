"""Shared real-Postgres fixtures for wiki v2 tests (skip when no DB).

These fixtures run against a real Postgres (testcontainers/compose or the local
scratch container, or a CI service container via ``WIKI_TEST_PG_DSN``). When no
database is reachable the tests skip cleanly, so `make test` stays green offline.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

_PG_DSN = os.environ.get("WIKI_TEST_PG_DSN", "postgresql://wiki:wiki@127.0.0.1:54329/wiki")

_TRUNCATE = (
    "TRUNCATE TABLE wiki.events, wiki.revisions, wiki.queries, wiki.links, "
    "wiki.page_tags, wiki.revision_sources, wiki.claims, wiki.source_bodies, "
    "wiki.run_files, wiki.pages, wiki.runs, wiki.tags, wiki.sources, wiki.files CASCADE"
)


def _pg_available() -> bool:
    """Probe the DSN once (cached).

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
    except Exception:  # noqa: BLE001 - a probe failure means "skip"
        return False


def reset_postgres() -> None:
    """Truncate all wiki tables for test isolation."""
    import psycopg

    with psycopg.connect(_PG_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(_TRUNCATE)


def pg_dsn() -> str:
    """Return the configured Postgres test DSN.

    Returns:
        str: DSN.
    """
    return _PG_DSN


has_pg = _pg_available()


@pytest.fixture
async def pg_store() -> AsyncIterator[Any]:
    """(skip-if-unavailable) A fresh PostgresWikiStore against the test DB.

    Yields:
        Any: A ``PostgresWikiStore`` backed by real Postgres.
    """
    if not has_pg:
        pytest.skip("WIKI_TEST_PG_DSN not reachable")
    from knowledge_engine.infrastructure.wiki import PostgresWikiStore

    reset_postgres()
    store = PostgresWikiStore.from_dsn(_PG_DSN)
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def pg_dsn_str() -> str:
    """Return the configured Postgres test DSN.

    Returns:
        str: DSN.
    """
    return _PG_DSN


@pytest.fixture
def pg_conn() -> Any:
    """(skip-if-unavailable) A direct sync psycopg connection, DB reset first.

    Returns:
        Any: A psycopg connection (autocommit) after truncating the wiki schema.
    """
    if not has_pg:
        pytest.skip("WIKI_TEST_PG_DSN not reachable")
    import psycopg

    reset_postgres()
    return psycopg.connect(_PG_DSN, autocommit=True)


@pytest.fixture
def pg_article(tmp_path) -> str:
    """Write a generic article file and return its path.

    Args:
        tmp_path: Pytest temp dir.

    Returns:
        str: Article path.
    """
    path = tmp_path / "article.md"
    path.write_text("Retrieval augmented generation reduces hallucinations in LLMs. " * 10, encoding="utf-8")
    return str(path)

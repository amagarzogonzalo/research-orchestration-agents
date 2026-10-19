"""Unit tests: explicit job-store selection (memory vs Postgres)."""

from __future__ import annotations

import pytest

from knowledge_engine.service.jobs import InMemoryJobStore, PostgresJobStore, build_job_store


def test_memory_kind_is_process_local() -> None:
    """``JOBS_KIND=memory`` needs no database."""
    assert isinstance(build_job_store("memory", ""), InMemoryJobStore)


def test_postgres_kind_requires_a_dsn() -> None:
    """A configured Postgres kind without a DSN fails loudly."""
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_job_store("postgres", "")
    assert isinstance(build_job_store("postgres", "postgresql://example/db"), PostgresJobStore)


def test_unknown_kind_is_rejected() -> None:
    """A typo cannot silently select a store."""
    with pytest.raises(ValueError, match="unknown JOBS_KIND"):
        build_job_store("redis", "")

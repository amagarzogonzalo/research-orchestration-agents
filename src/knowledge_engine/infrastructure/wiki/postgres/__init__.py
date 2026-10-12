"""Relational Postgres wiki adapter (v2 schema): store facade + vault linter.

Exposes the async composition root (:class:`PostgresWikiStore`, implements the
wiki ports) and its :class:`PostgresVaultLinter`. Persistence + connection
concerns live in their own modules (SRP); the store stays a thin orchestrator.
"""

from knowledge_engine.infrastructure.wiki.postgres.connection import AsyncPgConnectionFactory
from knowledge_engine.infrastructure.wiki.postgres.files import (
    FileStorePort,
    LocalFileStore,
    SupabaseFileStore,
    sha256_bytes,
    sharded_path,
)
from knowledge_engine.infrastructure.wiki.postgres.linter import PostgresVaultLinter
from knowledge_engine.infrastructure.wiki.postgres.store import PostgresWikiStore

__all__ = [
    "AsyncPgConnectionFactory",
    "FileStorePort",
    "LocalFileStore",
    "PostgresVaultLinter",
    "PostgresWikiStore",
    "SupabaseFileStore",
    "sha256_bytes",
    "sharded_path",
]

"""Async Postgres connection factory (SRP): pool ownership + transaction scopes.

The wiki adapter runs non-blocking: a ``psycopg_pool.AsyncConnectionPool`` hands
out async connections, and each operation borrows one through
:meth:`AsyncPgConnectionFactory.transaction` (atomic unit of work) or
:meth:`AsyncPgConnectionFactory.cursor` (read). Callers never touch a connection
or a pool directly (DIP).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from knowledge_engine.core.exceptions import ConfigurationError

_POOLER_SUFFIXES = (":6543", ":5432")


class AsyncPgConnectionFactory:
    """Provide async psycopg cursors/transactions to the wiki store.

    Args:
        pool: A psycopg_pool ``AsyncConnectionPool`` with a ``connection()``
            async context manager.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._opened = False
        self._open_lock: asyncio.Lock | None = None

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 5) -> AsyncPgConnectionFactory:
        """Build a factory over an async pool for a DSN.

        Supabase's transaction-pooler port (6543) cannot use prepared
        statements, so ``prepare_threshold`` is disabled there.

        Args:
            dsn: Postgres connection string.
            min_size: Minimum pool size.
            max_size: Maximum pool size.

        Returns:
            AsyncPgConnectionFactory: Configured factory.

        Raises:
            ConfigurationError: When ``psycopg[pool]`` is not installed.
        """
        try:
            from psycopg_pool import AsyncConnectionPool
        except ImportError as exc:
            raise ConfigurationError("the postgres wiki adapter needs psycopg[pool]") from exc
        # Supabase's transaction pooler (port 6543) cannot use prepared
        # statements, so disable prepare_threshold there (passed as a
        # per-connection kwarg; psycopg_pool has no attribute for it).
        prepare_threshold: int | None = None if dsn.split("@")[-1].startswith(_POOLER_SUFFIXES) else 5
        pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=15,
            open=False,
            kwargs={"prepare_threshold": prepare_threshold},
        )
        return cls(pool)

    async def _ensure_open(self) -> None:
        """Open the pool once, within the currently running loop.

        Opening inside the first cursor/transaction call guarantees the pool
        binds to the loop that will actually use it (required on Windows where
        psycopg-async only works on a SelectorEventLoop). ``open()`` is called
        without ``wait`` to avoid psycopg_pool's loop-bound full-event assert.
        """
        if self._opened:
            return
        if self._open_lock is None:
            self._open_lock = asyncio.Lock()
        async with self._open_lock:
            if not self._opened:
                await self._pool.open()
                self._opened = True

    @classmethod
    def from_env(cls) -> AsyncPgConnectionFactory:
        """Build a factory from ``DATABASE_URL`` (or ``SUPABASE_DB_URL``).

        Returns:
            AsyncPgConnectionFactory: Configured factory.

        Raises:
            ConfigurationError: When no DSN is set.
        """
        import os

        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        if not dsn:
            raise ConfigurationError("wiki_store_kind='postgres' needs DATABASE_URL (or SUPABASE_DB_URL)")
        return cls.from_dsn(dsn)

    async def close(self) -> None:
        """Close the underlying pool."""
        await self._pool.close()

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[Any]:
        """Borrow an autocommitted read cursor.

        Yields:
            Any: An async psycopg cursor.
        """
        await self._ensure_open()
        async with self._pool.connection() as aconn:
            await aconn.set_autocommit(True)
            cursor = aconn.cursor()
            try:
                yield cursor
            finally:
                await cursor.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Open one atomic unit of work; commit-or-rollback on exit.

        Yields:
            Any: An async psycopg cursor inside a transaction.

        Raises:
            BaseException: Re-raised after a rollback so the failure propagates.
        """
        await self._ensure_open()
        async with self._pool.connection() as aconn:
            await aconn.set_autocommit(False)
            cursor = aconn.cursor()
            try:
                yield cursor
                await aconn.commit()
            except BaseException:
                await aconn.rollback()
                raise
            finally:
                await cursor.close()


__all__ = ["AsyncPgConnectionFactory"]

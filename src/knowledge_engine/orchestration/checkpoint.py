"""Checkpointer factory — the ONLY checkpointer abstraction (ADR 0008 §3).

Default stays files-only: ``memory`` is the in-process default and ``file``
(:class:`FileCheckpointer`) is the durable, portable option — a folder
``research_store/checkpoints`` moves with the data.

``postgres`` is the opt-in cloud kind (Supabase Postgres via
``langgraph-checkpoint-postgres``): same LangGraph API, shared across
processes/containers, required for multi-worker/cloud + durable HITL. Needs the
``store`` extra (``uv sync --extra store``) and a session-pooler ``DATABASE_URL``.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from knowledge_engine.core.exceptions import ConfigurationError
from knowledge_engine.store.file_checkpointer import ALLOWED_CHANNEL_SERDE_TYPES, FileCheckpointer


def _postgres_dsn(**kwargs: Any) -> str:
    """Resolve the Postgres DSN from kwargs or the environment.

    Args:
        **kwargs: Optional ``conn_string``/``dsn`` override.

    Returns:
        str: Postgres connection string.

    Raises:
        ConfigurationError: When no DSN is configured.
    """
    dsn = kwargs.get("conn_string") or kwargs.get("dsn") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ConfigurationError(
            "postgres checkpointer needs a DSN: pass conn_string=... or set DATABASE_URL "
            "(Supabase session pooler, :5432, ?sslmode=require)"
        )
    return str(dsn)


def build_checkpointer(kind: str = "memory", **kwargs: Any) -> Any:
    """Return the checkpointer for ``kind``.

    Args:
        kind: ``memory`` (in-process, default), ``file`` (durable, portable),
            or ``postgres`` (Supabase, opt-in cloud).
        **kwargs: Passed to the chosen checkpointer (e.g. ``root`` for file,
            ``conn_string`` for postgres).

    Returns:
        Any: A LangGraph checkpointer — for ``postgres`` an async context
            manager yielding ``AsyncPostgresSaver`` (upstream API: no running
            loop exists at build time, so the saver can only be constructed
            inside ``async with``). Enter it once at boot, call ``setup()``,
            and keep it for the app lifetime (see ``ResearchEngine.setup``).

    Raises:
        ConfigurationError: For unknown kinds, a missing DSN, or a missing
            ``langgraph-checkpoint-postgres`` install.
    """
    if kind == "memory":
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_CHANNEL_SERDE_TYPES))
    if kind == "file":
        return FileCheckpointer(root=kwargs.get("root", "research_store/checkpoints"))
    if kind == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ConfigurationError("postgres checkpointer needs the 'store' extra: uv sync --extra store") from exc
        # AsyncIterator CM: connection opens on __aenter__ (needs a live
        # Supabase session-pooler DSN, :5432). Supabase: session pooler DSN.
        return AsyncPostgresSaver.from_conn_string(_postgres_dsn(**kwargs))
    raise ConfigurationError(f"unknown checkpointer kind: {kind!r} (use 'memory', 'file', or 'postgres')")


async def setup_postgres(*resources: Any) -> None:
    """Open pools and create tables for Postgres saver/store resources.

    Call once at boot (needs a live Supabase connection). Each resource is an
    ``AsyncPostgresSaver``/``AsyncPostgresStore`` from the factories above.

    Args:
        *resources: Saver/store instances to open and set up.
    """
    for resource in resources:
        pool = getattr(resource, "conn", None)
        if pool is not None and getattr(pool, "closed", False):
            await pool.open()
        setup = getattr(resource, "setup", None)
        if callable(setup):
            await setup()


__all__ = ["build_checkpointer", "setup_postgres"]

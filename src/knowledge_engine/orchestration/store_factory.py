"""Store factory — the ONLY long-term-memory abstraction.

Complements ``checkpoint.py``: the checkpointer holds per-``thread_id``
execution state (super-step snapshots, HITL resume); the store holds
cross-thread memory (``(user_id, memories)`` namespaces via
``runtime.store.aput/asearch``).

Default stays in-process (``memory``). ``postgres`` is the opt-in cloud kind
(Supabase Postgres via ``langgraph-checkpoint-postgres``): same ``DATABASE_URL``
as the checkpointer, separate tables (``store.setup()`` vs
``checkpointer.setup()``). Needs the ``store`` extra.
"""

from __future__ import annotations

import os
from typing import Any

from knowledge_engine.core.exceptions import ConfigurationError


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
            "postgres store needs a DSN: pass conn_string=... or set DATABASE_URL "
            "(Supabase session pooler, :5432, ?sslmode=require)"
        )
    return str(dsn)


def build_store(kind: str = "memory", **kwargs: Any) -> Any:
    """Return the LangGraph store for ``kind``.

    Args:
        kind: ``memory`` (in-process, default) or ``postgres`` (Supabase, opt-in).
        **kwargs: Passed to the chosen store (e.g. ``conn_string`` for postgres,
            ``index`` for semantic-search embeddings).

    Returns:
        Any: A LangGraph store — for ``postgres`` an async context manager
            yielding ``AsyncPostgresStore`` (same lifecycle as the checkpointer:
            enter once at boot, call ``setup()``, keep for the app lifetime).

    Raises:
        ConfigurationError: For unknown kinds, a missing DSN, or a missing
            ``langgraph-checkpoint-postgres`` install.
    """
    if kind == "memory":
        from langgraph.store.memory import InMemoryStore

        return InMemoryStore(index=kwargs.get("index"))
    if kind == "postgres":
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore
        except ImportError as exc:
            raise ConfigurationError("postgres store needs the 'store' extra: uv sync --extra store") from exc
        # AsyncIterator CM: connection opens on __aenter__ (see checkpoint.py).
        return AsyncPostgresStore.from_conn_string(_postgres_dsn(**kwargs), index=kwargs.get("index"))
    raise ConfigurationError(f"unknown store kind: {kind!r} (use 'memory' or 'postgres')")


__all__ = ["build_store"]

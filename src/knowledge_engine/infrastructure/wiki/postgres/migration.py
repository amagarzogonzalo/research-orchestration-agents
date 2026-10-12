"""Migration guard: verify the wiki v2 schema exists (NO runtime DDL).

The schema is applied by the checked-in migration file
``supabase/migrations/*_wiki_v2.sql`` (via ``supabase db push`` / CI). The
adapter must never run DDL at runtime; it only verifies that the expected
objects exist and fails fast with a clear message when they do not.
"""

from __future__ import annotations

from typing import Any

_CHECK = (
    "SELECT to_regclass('wiki.pages') IS NOT NULL "
    "AND to_regclass('wiki.revisions') IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
    "            WHERE n.nspname = 'wiki' AND p.proname = 'search_pages_filtered')"
)


async def ensure_schema(cur: Any) -> None:
    """Raise if the wiki v2 schema is not applied on the connected database.

    Args:
        cur: An async psycopg cursor.

    Raises:
        RuntimeError: When the wiki schema is missing.
    """
    await cur.execute(_CHECK)
    row = await cur.fetchone()
    if not row or not row[0]:
        raise RuntimeError(
            "wiki v2 schema not found on this database. Apply "
            "supabase/migrations/*_wiki_v2.sql (supabase db push) before using "
            "wiki_store_kind='postgres'."
        )


__all__ = ["ensure_schema"]

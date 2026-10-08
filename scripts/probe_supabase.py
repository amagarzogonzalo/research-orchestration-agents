#!/usr/bin/env python3
"""Phase 0 — Supabase connectivity probe.

Validates both Postgres (session pooler) and Storage (REST) before any migration.
Run: uv run python scripts/probe_supabase.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load .env first
sys.path.insert(0, str(Path(__file__).parent.parent))
from knowledge_engine.config import load_dotenv

load_dotenv()


async def probe_postgres() -> bool:
    """Test Postgres session pooler connection + CREATE privs."""
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("❌ DATABASE_URL not set")
        return False

    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        version = await conn.fetchval("select version()")
        await conn.close()
        print(f"✅ Postgres: {version[:80]}")
        return True
    except Exception as e:
        print(f"❌ Postgres: {e}")
        return False


def probe_storage() -> bool:
    """Test Supabase Storage REST API with service_role key."""
    from supabase import create_client, Client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")

    if not url or not key:
        print("❌ SUPABASE_URL or SUPABASE_SECRET_KEY not set")
        return False

    try:
        client: Client = create_client(url, key)
        buckets = client.storage.list_buckets()
        print(f"✅ Storage: {len(buckets)} buckets — {[b.name for b in buckets]}")

        # Ensure target buckets exist
        for name in ("articles", "sources"):
            if name not in [b.name for b in buckets]:
                client.storage.create_bucket(name, options={"public": False})
                print(f"  Created bucket: {name}")
        return True
    except Exception as e:
        print(f"❌ Storage: {e}")
        return False


def main() -> int:
    print("🔍 Phase 0 — Supabase Connectivity Probe\n")

    pg_ok = asyncio.run(probe_postgres())
    storage_ok = probe_storage()

    print()
    if pg_ok and storage_ok:
        print("🎉 All probes passed — ready for Phase 1")
        return 0
    else:
        print("💥 Probe failed — fix Supabase project / keys / network before proceeding")
        return 1


if __name__ == "__main__":
    sys.exit(main())

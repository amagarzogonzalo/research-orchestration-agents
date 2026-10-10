#!/usr/bin/env python3
"""Supabase wiki activation probe — the step a human runs once with keys.

Creates the required buckets and prints the exact engine invocation that
activates the cloud wiki mirror. No secrets are printed or committed; the
script fails loudly (exit 1) when either key is missing so config drift is
visible (AGENTS rule 5: a failed lookup is reported, never papered over).

    SUPABASE_URL=... SUPABASE_SECRET_KEY=... uv run python bin/wiki_supabase_provision.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")


def main() -> int:
    """Probe Supabase and create the wiki bucket.

    Returns:
        int: 0 on success, 1 when keys are missing or the probe fails.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY not set — nothing provisioned (no-op).")
        return 1
    try:
        from supabase import create_client
    except ImportError:
        print("supabase extra not installed: uv sync --extra supabase")
        return 1

    client = create_client(url, key)
    try:
        existing = {b.name: b.public for b in client.storage.list_buckets()}
    except Exception as exc:  # noqa: BLE001 - surface connectivity errors exactly
        print(f"storage list failed: {exc}")
        return 1
    buckets = {"wiki": False}
    for name, public in buckets.items():
        if name not in existing:
            client.storage.create_bucket(name, options={"public": public})
            print(f"created bucket: {name} (public={public})")
        else:
            print(f"bucket present : {name}")
    print("probe ok — Supabase wiki mirror is ready to activate.")
    print("activate with:")
    print("  engine = ResearchEngine(store_root='research_store', wiki_store_kind='supabase')")
    print("  await engine.setup()   # when using postgres checkpoint/store kinds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

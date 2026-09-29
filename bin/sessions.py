#!/usr/bin/env python3
"""What your agent sessions actually cost, per model.

    python bin/sessions.py            # last 30 days
    python bin/sessions.py --days 7

Reads opencode's local session storage. Per-token price is the wrong thing to
optimize: a cheap model that needs six turns where another needs two is more
expensive AND slower. What matters is cost per completed task, so this reports
turns-per-session alongside spend.

Storage layout differs across opencode versions; if nothing is found, pass
--path with your session directory (see `opencode --help` for its data dir).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

CANDIDATES = [
    Path.home() / ".local/share/opencode/storage",
    Path.home() / ".local/share/opencode",
    Path.home() / "Library/Application Support/opencode",
    Path(os.environ.get("XDG_DATA_HOME", "/nonexistent")) / "opencode",
]


def find_root(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    return next((p for p in CANDIDATES if p.exists()), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--path")
    args = ap.parse_args()

    root = find_root(args.path)
    if root is None:
        print("no opencode session storage found. Pass --path <dir>.")
        return 0

    cutoff = time.time() - args.days * 86400
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"in": 0, "out": 0, "cost": 0.0, "turns": 0, "sessions": 0}
    )
    sessions_seen: dict[str, set] = defaultdict(set)

    for f in root.rglob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for rec in data if isinstance(data, list) else [data]:
            if not isinstance(rec, dict):
                continue
            tok = rec.get("tokens") or rec.get("usage")
            model = rec.get("modelID") or rec.get("model")
            if not tok or not model:
                continue
            m = by_model[str(model)]
            m["in"] += tok.get("input", tok.get("prompt_tokens", 0)) or 0
            m["out"] += tok.get("output", tok.get("completion_tokens", 0)) or 0
            m["cost"] += float(rec.get("cost", 0) or 0)
            m["turns"] += 1
            if sid := rec.get("sessionID"):
                sessions_seen[str(model)].add(sid)

    if not by_model:
        print(f"no usage records under {root} in the last {args.days} days.")
        print(
            "opencode's storage format varies by version; if you know you have "
            "sessions, the parser above needs the field names from your files."
        )
        return 0

    w = max(len(m) for m in by_model)
    print(
        f"{'model'.ljust(w)}  {'in':>10} {'out':>10} {'turns':>6} {'sess':>5} "
        f"{'turns/sess':>10} {'cost':>9}"
    )
    for model, m in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        n = len(sessions_seen[model]) or 1
        print(
            f"{model.ljust(w)}  {int(m['in']):>10} {int(m['out']):>10} "
            f"{int(m['turns']):>6} {n:>5} {m['turns'] / n:>10.1f} ${m['cost']:>8.2f}"
        )
    print("\nHigh turns/session on a cheap model usually means it is not cheap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

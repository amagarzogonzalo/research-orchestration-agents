#!/usr/bin/env python3
"""Write or verify the committed OpenAPI snapshot (``docs/openapi.json``).

No database, network, or API keys are needed: the app factory is built with an
inert engine double and the in-memory job store, then inspected.

    uv run python bin/export_openapi.py            # regenerate
    uv run python bin/export_openapi.py --check    # fail if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from knowledge_engine.service.app import create_app
from knowledge_engine.service.jobs import InMemoryJobStore

_DEFAULT_PATH = Path("docs/openapi.json")


def _spec() -> dict:
    """Build the service OpenAPI document without side effects.

    Returns:
        dict: The OpenAPI mapping.
    """
    app = create_app(engine=object(), jobs=InMemoryJobStore(), enable_docs=True)
    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    """Write or check the snapshot.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        int: 0 on success, 1 when ``--check`` finds a stale snapshot.
    """
    parser = argparse.ArgumentParser(description="Export/check the service OpenAPI snapshot.")
    parser.add_argument("--check", action="store_true", help="fail when the file is stale")
    parser.add_argument("--path", default=str(_DEFAULT_PATH), help="snapshot path")
    args = parser.parse_args(argv)

    path = Path(args.path)
    rendered = json.dumps(_spec(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current != rendered:
            print(f"stale OpenAPI snapshot: {path} (run: uv run python bin/export_openapi.py)")
            return 1
        print(f"OpenAPI snapshot is current: {path}")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

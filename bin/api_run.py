#!/usr/bin/env python3
"""Local dev entrypoint for the HTTP service.

    uv run python bin/api_run.py           # http://127.0.0.1:8000
    PORT=9000 uv run python bin/api_run.py

Prod uses the Dockerfile/uvicorn directly; this is the local convenience.
"""

from __future__ import annotations

import asyncio
import os
import sys


def main() -> int:
    """Run uvicorn against the service app.

    Returns:
        int: Process exit code.
    """
    import uvicorn

    # psycopg async connections require the selector loop; Windows defaults to
    # Proactor, which would break the job store and readiness probes.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "knowledge_engine.service.api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

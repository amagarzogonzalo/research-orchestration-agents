"""Event-driven worker (ADR 0007).

Runs the choreography loop: consumes every station's inbox stream and dispatches
events. Must be started *after* a producer existed or with ``--submit`` to seed the
first event, since a brand-new empty stream has no messages yet.

    python -m knowledge_engine.worker --submit "<query>"      # emit first event
    python -m knowledge_engine.worker --consume                # start the loop
    # or combine:
    python -m knowledge_engine.worker --query "<query>"        # submit then consume
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _build() -> Any:
    """Build the event pipeline.

    Returns:
        Any: Configured event pipeline.
    """
    from knowledge_engine.container import build_event_pipeline

    return build_event_pipeline()


def _thread_id(query: str) -> str:
    """Derive a stable thread id from a query.

        Uses SHA-1 because ``hash()`` is seeded per-process and would yield a
        different id on every invocation.

    Args:
        query: Scholarly query string.

    Returns:
        str: Result text.

    """
    return f"t-{hashlib.sha1(query.strip().encode('utf-8')).hexdigest()[:12]}"


async def _run(query: str | None, consume: bool) -> None:
    """Run the unit of work.

    Args:
        query: Scholarly query string.
        consume: Whether to consume streams.
    """
    pipeline = _build()
    if query:
        thread_id = _thread_id(query)
        logger.info("submitting research request %r (thread=%s)", query, thread_id)
        await pipeline.submit(thread_id=thread_id, query=query)
    if consume:
        logger.info("consuming event streams...")
        await pipeline.consume_forever(consumer="worker-1")
    else:
        await pipeline.close()


def main() -> None:
    """Run the entrypoint."""
    parser = argparse.ArgumentParser(description="Event-driven knowledge engine worker.")
    parser.add_argument("--query", help="research query to submit")
    parser.add_argument(
        "--consume", action="store_true", help="run the event loop (default off unless --query also passed)"
    )
    args = parser.parse_args()
    consume = args.consume or bool(args.query)
    asyncio.run(_run(args.query, consume))


if __name__ == "__main__":
    main()

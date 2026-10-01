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
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _build():
    from knowledge_engine.container import build_event_pipeline

    return build_event_pipeline()


async def _run(query: str | None, consume: bool) -> None:
    pipeline = _build()
    if query:
        thread_id = f"t-{abs(hash(query)):x}"
        logger.info("submitting research request %r (thread=%s)", query, thread_id)
        await pipeline.submit(thread_id=thread_id, query=query)
    if consume:
        logger.info("consuming event streams...")
        await pipeline.consume_forever(consumer="worker-1")
    else:
        await pipeline.close()


def main() -> None:
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

"""Postgres cutover integration (issue #18): engine boots PG kinds and resumes.

Gated by ``RUN_LIVE_E2E=1`` (repo convention). Verifies: setup() opens the
Supabase session pooler, a full research run checkpoints into Postgres, and
aget_state recovers the thread from Postgres — the durability thesis.
"""

from __future__ import annotations

import asyncio
import os

import pytest

if os.name == "nt":
    # psycopg async requires SelectorEventLoop on Windows; production is Linux.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _SELECTOR_POLICY_SET = True
else:
    _SELECTOR_POLICY_SET = False


def _gated() -> None:
    if os.environ.get("RUN_LIVE_E2E", "") != "1":
        pytest.skip("set RUN_LIVE_E2E=1 to run the Postgres cutover")
    from knowledge_engine.config import load_dotenv

    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured")


@pytest.mark.live
async def test_engine_run_checkpoints_into_postgres(tmp_path) -> None:
    """research() with postgres checkpointer/state survives to the server."""
    _gated()
    import uuid

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    from knowledge_engine.engine import ResearchEngine
    from tests.conftest import FakeArtifactStore, FakeProvider, FakeScrape, FakeSearch

    engine = ResearchEngine(
        store_root=str(tmp_path / "runs"),
        checkpointer_kind="postgres",
        store_kind="postgres",
        artifact_store_kind="supabase",
        body_store_kind="supabase",
        artifacts=FakeArtifactStore(),
        search=FakeSearch(),
        scrape=FakeScrape(),
        provider=FakeProvider(),
    )
    await engine.setup()
    thread_id = f"pg-cutover-{uuid.uuid4().hex[:8]}"
    try:
        result = await engine.research("multi-agent orchestration", workflow="research", thread_id=thread_id)
        assert result.state.status.value == "done"

        # The thread is durable in Postgres: state reads back from the saver.
        runtime = engine._runtime_for("research")
        snapshot = await runtime.get_state("research", thread_id)
        assert snapshot.state.status.value == "done"
        assert snapshot.state.sources, "sources recovered from Postgres"

        # The saver is the postgres one (parity proof, not the file saver).
        assert isinstance(engine.checkpointer, AsyncPostgresSaver)
        assert isinstance(engine.memory_store, AsyncPostgresStore)

        graph = engine._runtime_for("research").graph("research")
        history = [
            c async for c in graph.aget_state_history({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        ]
        assert len(history) >= 5, "superstep history durably held in Postgres"
    finally:
        await engine.close()

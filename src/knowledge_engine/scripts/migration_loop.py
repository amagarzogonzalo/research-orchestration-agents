"""Migration verification loop runner (issues #18/#20, executed autonomously).

Runs 2 use cases x 2 workflows (research + deep) on the FULL CLOUD STACK:
postgres checkpointer/store + Supabase Storage artifacts/bodies. Then verifies
what landed where: state readable from Postgres, article + sources readable
back from Storage, citations, BodyRef-only channels.

Fails loudly on any leg; findings print at the end for the work-issue report.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

if os.name == "nt":
    # psycopg async on Windows needs the selector loop; Linux prod unaffected.
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is not None:
        asyncio.set_event_loop_policy(policy())

from typing import Any

from knowledge_engine.config import load_dotenv

USE_CASES = {
    "A": (
        "Compare agentic AI frameworks for orchestrating LLM research workflows: "
        "LangGraph vs OpenAI Agents SDK vs CrewAI"
    ),
    "B": "How does retrieval-augmented generation reduce hallucinations in large language models",
}


async def run_leg(engine: Any, use_case: str, query: str, workflow: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """One live leg; returns (run_evidence, checks).

    Args:
        engine: Cloud-wired ResearchEngine.
        use_case: A/B id.
        query: Research query.
        workflow: research|deep.

    Returns:
        tuple[dict[str, Any], dict[str, Any]]: Evidence + checks dicts.
    """
    thread = f"loop-{use_case}-{workflow}-{uuid.uuid4().hex[:6]}"
    t0 = time.perf_counter()
    result = await engine.research(query, workflow=workflow, thread_id=thread)
    wall = time.perf_counter() - t0
    state = result.state
    checks = {
        "thread": thread,
        "workflow": workflow,
        "use_case": use_case,
        "status": state.status.value,
        "wall_s": round(wall, 1),
        "sources": len(state.sources),
        "bodies": len(state.bodies),
        "refs_only": sorted({type(b).__name__ for b in state.bodies}) == ["BodyRef"] if state.bodies else True,
        "output_path": bool(state.output_path),
        "pg_history": 0,
        "storage_article": False,
        "storage_sources": False,
    }
    evidence_run = {"query": query, "wall_s": checks["wall_s"], "thread": thread}
    # Postgres leg: history must exist for this thread
    graph = engine._runtime_for(workflow).graph(workflow)
    try:
        history = [
            c async for c in graph.aget_state_history({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
        ]
        checks["pg_history"] = len(history)
    except Exception as exc:  # noqa: BLE001 - loop must surface the leg failure legibly
        evidence_run["pg_error"] = f"{type(exc).__name__}: {exc}"[:160]
    # Storage leg: article + sources readable back
    from knowledge_engine.store.supabase_artifact_store import SupabaseArtifactStore

    store = SupabaseArtifactStore.from_env()
    article = store.get_article(thread)
    checks["storage_article"] = bool(article)
    sources = store.get_sources(thread)
    checks["storage_sources"] = bool(sources)
    if article and state.output_path:
        import re

        citations = sorted(set(re.findall(r"\[\^([A-Za-z0-9_.:/-]+)\]", article)))
        checks["citations"] = len(citations)
        web = [c for c in citations if c.startswith(("duckduckgo", "tavily", "exa", "serper", "wikipedia"))]
        checks["web_citations"] = len(web)
        evidence_run["citations"] = len(citations)
        evidence_run["web_citations"] = len(web)
    return checks, evidence_run


async def main(loop_id: str, root: str) -> dict[str, Any]:
    """Run the full loop.

    Args:
        loop_id: Which loop is running (used in thread prefix).
        root: Store root for run folders.

    Returns:
        dict[str, Any]: Summary for the findings issue.
    """
    if not load_dotenv():
        print("FAIL env missing")
        sys.exit(1)
    if os.name == "nt":
        import asyncio as _a

        _a.set_event_loop_policy(_a.WindowsSelectorEventLoopPolicy())
    from knowledge_engine.engine import ResearchEngine

    engine = ResearchEngine(
        store_root=root,
        checkpointer_kind="postgres",
        store_kind="postgres",
        artifact_store_kind="supabase",
        body_store_kind="supabase",
    )
    await engine.setup()
    results = []
    try:
        for use_case, query in USE_CASES.items():
            for workflow in ("research", "deep"):
                checks, _evidence = await run_leg(engine, use_case, query, workflow)
                results.append(checks)
                ok = checks["status"] == "done" and checks["pg_history"] > 0 and checks["storage_article"]
                print(f"{'PASS' if ok else 'FAIL'} {use_case}/{workflow}: {json.dumps(checks, default=str)}")
    finally:
        await engine.close()
    all_ok = all(r["status"] == "done" and r["pg_history"] > 0 and r["storage_article"] for r in results)
    summary = {"loop": loop_id, "ok": all_ok, "runs": results}
    out_dir = Path(f"research_store/{root}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "loop-report.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"LOOP {loop_id}: {'GREEN' if all_ok else 'RED'} report={out}")
    return summary


if __name__ == "__main__":
    loop = sys.argv[1] if len(sys.argv) > 1 else "1"
    asyncio.run(main(loop, f"loop-{loop}"))

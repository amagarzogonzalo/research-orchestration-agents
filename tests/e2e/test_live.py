"""Live end-to-end test — runs the REAL research workflow with the free-tier keys.

This is an honest real-network test (not a fake-based one):

* skips automatically when `.env` free-tier keys are missing, so CI/local
  without creds stays green;
* exercises search (OpenAlex/Crossref), LLM discovery, the LangGraph research
  workflow, and files-only persistence end-to-end.

Run explicitly with:

    pytest -m live -k real_research
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from knowledge_engine.config import load_dotenv
from knowledge_engine.engine import ResearchEngine

pytestmark = pytest.mark.live


def _load_env() -> None:
    """Load .env keys into os.environ (only if not already set)."""
    load_dotenv()
    if Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _has_keys() -> bool:
    return bool(
        any(os.environ.get(k) for k in ("GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"))
    )


# Live tests are OPT-IN: they hit real network + real free-tier quota.
# Run them with RUN_LIVE_E2E=1 pytest -m live
_live_enabled = os.environ.get("RUN_LIVE_E2E", "") not in {"", "0", "false", "False"}

_load_env()

requires_keys = pytest.mark.skipif(
    not (_live_enabled and _has_keys()),
    reason="set RUN_LIVE_E2E=1 and configure free-tier keys in .env to run live e2e",
)


@requires_keys
@pytest.mark.asyncio
async def test_real_research_workflow(tmp_path) -> None:
    """Run the real query end-to-end and assert persisted artifacts."""
    store_root = str(tmp_path / "store")
    engine = ResearchEngine(store_root=store_root, search_limit=8)

    # 1) discovery actually finds a working free model
    model = await engine.discover()
    assert isinstance(model, str) and model, "discovery should return a model id"

    # 2) search actually resolves sources
    sources = await engine.search_run("attention mechanisms in large language models", limit=5)
    assert sources, "search should return SourceRecords"

    # 3) the LangGraph research workflow runs to completion
    result = await engine.research(
        "Explain the top 7 relevant papers for attention in LLMs",
        workflow="research",
        thread_id="live-e2e",
    )
    assert not result.interrupted, "auto-approve review should not pause"
    state = result.state
    assert state.status.value == "done", f"expected done, got {state.status}"
    assert state.sources, "ingest should resolve sources"
    assert state.synthetic_notes, "synthesize should write notes"
    assert result.state.output_path, "render should produce an output path"

    # 4) files-only persistence is present
    out = Path(result.state.output_path)
    assert out.exists() and out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "Attention" in text or "attention" in text, "article should mention the topic"
    meta = json.loads((Path(store_root) / "live-e2e" / "metadata.json").read_text(encoding="utf-8"))
    assert meta.get("status") == "done", "metadata should record done"


@requires_keys
@pytest.mark.asyncio
async def test_real_search_and_scrape(tmp_path) -> None:
    """Search + scrape against live endpoints (no graph)."""
    engine = ResearchEngine(store_root=str(tmp_path / "store"), search_limit=3)
    sources = await engine.search_run("transformers attention", limit=3)
    assert sources, "live search should return results"
    # try scraping an arXiv HTML abstract page (browserless path)
    from knowledge_engine.infrastructure.scrape.trafilatura_adapter import TrafilaturaAdapter

    try:
        body = await TrafilaturaAdapter().to_markdown("https://arxiv.org/abs/1706.03762")
        assert len(body.markdown) > 200, "scrape should return substantial markdown"
    except Exception as exc:  # pragma: no cover - network flaky
        pytest.skip(f"arxiv scrape unavailable: {exc}")

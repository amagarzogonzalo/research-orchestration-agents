"""State-v2 wiring (W4, ADR 0013): fetch persists bodies, state carries BodyRefs.

Two guarantees: (1) ``FetchBodyNode`` writes refs and never leaks raw markdown
into state/checkpoints; (2) synthesis from refs is byte-identical to synthesis
from inline DocumentBody bodies — which is what keeps eval cassette keys
(replay) untouched.
"""

from __future__ import annotations

import json
from typing import Any

from knowledge_engine.orchestration.nodes.bodies import FetchBodyNode
from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode, _materialize_bodies
from knowledge_engine.schemas.source import BodyRef, DocumentBody
from knowledge_engine.store.body_file_store import LocalBodyStorage
from tests.conftest import FakeProvider, FakeScrape


async def test_fetch_writes_ref_not_markdown(tmp_path) -> None:
    """The bodies channel update contains a BodyRef; raw markdown never appears."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    fetch = FetchBodyNode(FakeScrape(), storage=store)
    source = {
        "id": "arxiv:2404.08189",
        "title": "Reducing hallucination",
        "source": "arxiv",
        "abstract": None,
        "doi": None,
        "pdf_url": None,
    }
    result = await fetch.run({"source": source})
    bodies = result.get("bodies", [])
    assert len(bodies) == 1
    ref = bodies[0]
    assert isinstance(ref, BodyRef)
    assert "markdown" not in json.dumps(result, default=str)
    assert ref.char_count > 0


async def test_synthesis_from_refs_is_byte_identical(tmp_path) -> None:
    """Same content inline (DocumentBody) vs via storage (BodyRef) -> same prompt bytes."""
    markdown = "# Paper\n" + ("Grounded claim 37% reduction with citation. " * 120)
    url = "https://arxiv.org/abs/2404.08189"
    inline_state: dict[str, Any] = {
        "query": "How does RAG reduce hallucinations",
        "sources": [],
        "filtered": [],
        "bodies": [DocumentBody(url=url, title="Paper", markdown=markdown)],
    }
    store = LocalBodyStorage(root=tmp_path / "bodies")
    ref = await store.save(DocumentBody(url=url, title="Paper", markdown=markdown))
    refs_state: dict[str, Any] = {
        "query": "How does RAG reduce hallucinations",
        "sources": [],
        "filtered": [],
        "bodies": [ref],
    }
    inline_prompt = SynthesizeNode._build_prompt(inline_state, None)  # type: ignore[arg-type]
    materialized = await _materialize_bodies(refs_state, store)  # type: ignore[arg-type]
    refs_prompt = SynthesizeNode._build_prompt({**refs_state, "bodies": materialized}, None)  # type: ignore[arg-type]
    assert refs_prompt == inline_prompt


async def test_run_with_refs_without_storage_fails_loudly() -> None:
    """Refs in state with no storage wired is a loud error, never silent data loss."""
    refs_state: dict[str, Any] = {
        "query": "q",
        "thread_id": "t",
        "sources": [],
        "filtered": [],
        "bodies": [BodyRef(url="https://x", title="X", path="/nonexistent/x.md")],
        "iterations": 0,
    }
    import pytest

    from knowledge_engine.core.exceptions import KnowledgeEngineError

    synth = SynthesizeNode(FakeProvider(), storage=None)  # type: ignore[arg-type]
    with pytest.raises(KnowledgeEngineError):
        await synth.run(refs_state)  # type: ignore[arg-type]


async def test_run_writes_notes_via_refs(tmp_path) -> None:
    """End-to-end through the node: refs in state -> synthesize loads -> notes written."""
    store = LocalBodyStorage(root=tmp_path / "bodies")
    fetch = FetchBodyNode(FakeScrape(), storage=store)
    source = {
        "id": "arxiv:2404.08189",
        "title": "T",
        "source": "arxiv",
        "abstract": None,
        "doi": None,
        "pdf_url": None,
    }
    fetch_result = await fetch.run({"source": source})
    synth = SynthesizeNode(FakeProvider(), storage=store)
    state: dict[str, Any] = {
        "query": "q",
        "thread_id": "t",
        "sources": [source],
        "filtered": [source],
        "bodies": fetch_result["bodies"],
        "iterations": 0,
    }
    update = await synth.run(state)  # type: ignore[arg-type]
    assert update["notes"], "synthesis must produce notes from ref-loaded evidence"

"""Unit tests: Loop 1 quality — extraction flow, drafts, fail-closed gates."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fakes import FakeWikiStore  # noqa: E402

from knowledge_engine.core.exceptions import KnowledgeEngineError  # noqa: E402
from knowledge_engine.orchestration.nodes.wiki_ingest import WikiIngestNode  # noqa: E402
from knowledge_engine.schemas.provider import UsageSummary  # noqa: E402
from knowledge_engine.schemas.wiki import WikiExtraction  # noqa: E402


class FakeProvider:
    """ProviderPort stub returning canned JSON per system prompt.

    Attributes:
        extract_json: Reply for the librarian (extraction) call.
        thesis_json: Reply for the thesis call.
        calls: Number of complete() calls.
    """

    def __init__(self, extract_json: str, thesis_json: str | None = None) -> None:
        self.extract_json = extract_json
        self.thesis_json = thesis_json
        self.calls = 0

    async def complete(self, req: Any) -> Any:
        """Reply with the canned JSON for the calling step.

        Args:
            req: Chat request.

        Returns:
            Any: ChatResult-like stub (message.content + usage).
        """
        self.calls += 1
        content = self.thesis_json if "thesis" in req.messages[0].content else self.extract_json
        return type("R", (), {"message": type("M", (), {"content": content})(), "usage": UsageSummary()})()

    async def extract(self, req: Any) -> object:
        """Unused by the wiki node.

        Args:
            req: Extraction request.

        Returns:
            object: Empty payload.
        """
        return {}


class _Result:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})()


async def test_extraction_off_by_default_no_provider_calls() -> None:
    """Without settings.wiki_extract the node never calls the provider."""
    store = FakeWikiStore()
    provider = FakeProvider('{"concepts": [], "entities": [], "summary": "", "contradictions": []}')
    node = WikiIngestNode(store, provider)
    out = await node.run({"thread_id": "t1", "query": "rag", "output_path": "x.md"})
    assert provider.calls == 0
    assert "llm_calls" not in out
    assert store.calls_ingest == 1


async def test_save_answers_when_opt_in() -> None:
    """settings.wiki_save_answers files the rendered answer into queries/."""
    store = FakeWikiStore()
    node = WikiIngestNode(store)
    out = await node.run(
        {"thread_id": "t1", "query": "rag", "output_path": "x.md", "settings": {"wiki_save_answers": True}}
    )
    assert store.calls_answer == 1
    assert out["wiki_answer"] is not None
    assert out["wiki_pages"] == [out["wiki_answer"]]


async def test_extraction_opt_in_files_drafts() -> None:
    """settings.wiki_extract + provider -> drafts filed, llm_calls delta 1."""
    store = FakeWikiStore()
    reply = (
        '{"concepts": ["Retrieval Augmented Generation"], "entities": ["Meta AI"], '
        '"summary": "RAG grounds answers in retrieved documents.", "contradictions": []}'
    )
    provider = FakeProvider(reply)
    node = WikiIngestNode(store, provider)
    out = await node.run(
        {"thread_id": "t1", "query": "rag", "output_path": "x.md", "settings": {"wiki_extract": True}}
    )
    assert store.calls_compile == 1
    assert out["llm_calls"] == 1
    assert len(out["wiki_pages"]) == 2
    assert store.last_compile is not None
    assert store.last_compile["extraction"].concepts == ["Retrieval Augmented Generation"]


async def test_extraction_degrades_after_source_page() -> None:
    """Extraction failure records an error note; run + source page survive.

    The deterministic source page is guaranteed first; an optional enrichment
    failure must never sink the research deliverable or write poison.
    """
    store = FakeWikiStore()
    provider = FakeProvider("not json at all")
    node = WikiIngestNode(store, provider)
    out = await node.run(
        {"thread_id": "t1", "query": "rag", "output_path": "x.md", "settings": {"wiki_extract": True}}
    )
    assert store.calls_ingest == 1, "source page must be guaranteed before the failure"
    assert store.calls_compile == 0, "nothing poisonous is filed on failure"
    assert "wiki_extract_error" in out, "the failure is reported for observability"
    assert "llm_calls" not in out, "no LLM call counted for a failed enrichment"


async def test_placeholder_names_rejected_by_schema_gate() -> None:
    """n/a/unknown-style names fail validation (fail-closed write gate)."""
    with pytest.raises(Exception):  # noqa: B017, PT011 - pydantic ValidationError
        WikiExtraction.model_validate({"concepts": ["n/a"], "entities": []})


async def test_thesis_files_verdict_page_and_degrades_on_failure() -> None:
    """Thesis opt-in files the page; provider failure degrades to a note."""
    store = FakeWikiStore()
    thesis_reply = (
        '{"claim": "RAG reduces hallucinations", "for_claims": ["a"], "against_claims": ["b"], '
        '"verdict": "supported", "sources": ["rag"]}'
    )
    good = FakeProvider('{"concepts": [], "entities": [], "summary": "", "contradictions": []}', thesis_reply)
    node = WikiIngestNode(store, good)
    out = await node.run(
        {
            "thread_id": "t1",
            "query": "rag",
            "output_path": "x.md",
            "notes": ["note one"],
            "settings": {"wiki_extract": True, "thesis": "RAG reduces hallucinations"},
        }
    )
    assert store.calls_thesis == 1
    assert out["llm_calls"] == 2
    assert any("synthesis" in p for p in out["wiki_pages"])

    bad = FakeProvider('{"concepts": [], "entities": [], "summary": "", "contradictions": []}', "broken")
    node2 = WikiIngestNode(store, bad)
    out2 = await node2.run(
        {
            "thread_id": "t2",
            "query": "rag",
            "output_path": "x.md",
            "settings": {"wiki_extract": True, "thesis": "claim"},
        }
    )
    assert out2["llm_calls"] == 1, "thesis failure must not abort the run"


async def test_concept_page_cap_enforced(tmp_path: Path) -> None:
    """max_wiki_pages caps the drafts recorded in state."""
    store = FakeWikiStore()
    names = ", ".join(f'"Concept {i}"' for i in range(12))
    provider = FakeProvider(f'{{"concepts": [{names}], "entities": [], "summary": "", "contradictions": []}}')
    node = WikiIngestNode(store, provider)
    out = await node.run(
        {
            "thread_id": "t1",
            "query": "rag",
            "output_path": "x.md",
            "settings": {"wiki_extract": True, "max_wiki_pages": 5},
        }
    )
    assert len(out["wiki_pages"]) == 5


async def test_file_drafts_review_api(tmp_path: Path) -> None:
    """FileWikiStore drafts: compile -> approve publishes; reject records feedback."""
    from knowledge_engine.infrastructure.wiki import FileWikiStore
    from knowledge_engine.schemas.wiki import WikiExtraction

    store = FileWikiStore(root=tmp_path / "wiki")
    extraction = WikiExtraction(concepts=["Vector Databases"], entities=[], summary="Stores embeddings.")
    drafts = await store.compile_extraction("t1", "rag hallucinations", extraction, ["needs citations"])
    assert drafts and (tmp_path / "wiki" / "drafts").exists()

    feedback = await store.feedback_for("rag hallucinations")
    assert feedback == []  # nothing rejected yet

    # Hand-edit guard: modify the draft body -> approval must refuse.
    draft = Path(drafts[0])
    draft.write_text(draft.read_text(encoding="utf-8") + "\nHAND EDIT\n", encoding="utf-8")
    with pytest.raises(KnowledgeEngineError):
        store.approve_draft("vector-databases")

    # Reject records feedback; next compile receives it.
    store.reject_draft("vector-databases", "add citations", query="rag hallucinations")
    assert await store.feedback_for("rag hallucinations") == ["add citations"]
    assert not draft.exists()

    # Fresh compile, untouched draft -> approval publishes into concepts/.
    drafts2 = await store.compile_extraction(
        "t2", "rag hallucinations", extraction, await store.feedback_for("rag hallucinations")
    )
    published = store.approve_draft("vector-databases")
    assert Path(published).exists() and "concepts" in published
    assert not Path(drafts2[0]).exists()
    hits = await store.search_wiki("vector databases")
    assert hits, "approved page must be searchable"


async def test_file_thesis_or_contradiction(tmp_path: Path) -> None:
    """flag_contradiction marks a page disputed and keeps prior content."""
    from knowledge_engine.infrastructure.wiki import FileWikiStore
    from knowledge_engine.schemas.wiki import WikiExtraction

    store = FileWikiStore(root=tmp_path / "wiki")
    await store.compile_extraction(
        "t1",
        "rag hallucinations",
        WikiExtraction(concepts=["Retrieval Augmented Generation"], entities=[], summary="x"),
        [],
    )
    store.approve_draft("retrieval-augmented-generation")
    loc = store.flag_contradiction("retrieval-augmented-generation", "later work disputes the mechanism")
    assert loc is not None
    assert store.disputed_slugs() == ["retrieval-augmented-generation"]
    text = Path(loc).read_text(encoding="utf-8")
    assert "later work disputes the mechanism" in text
    assert "Retrieval Augmented Generation" in text, "prior content must survive (close-never-delete)"
    log = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "disputed" in log


async def test_rejection_auto_block_after_five_strikes(tmp_path: Path) -> None:
    """5 rejections block the draft; approval refuses until unblocked."""
    from knowledge_engine.infrastructure.wiki import FileWikiStore
    from knowledge_engine.schemas.wiki import WikiExtraction

    store = FileWikiStore(root=tmp_path / "wiki")
    extraction = WikiExtraction(concepts=["Never Approved"], entities=[], summary="x")
    for i in range(5):
        await store.compile_extraction("t1", "rag hallucinations", extraction, [])
        store.reject_draft("never-approved", f"rejection {i}", query="rag hallucinations")
    assert store.blocked_slugs() == ["never-approved"]

    # Approval of a freshly compiled draft is refused (blocked).
    await store.compile_extraction("t1", "rag hallucinations", extraction, [])
    try:
        store.approve_draft("never-approved")
        raise AssertionError("blocked draft must refuse approval")
    except Exception as exc:  # noqa: BLE001 - blocked refusals are KnowledgeEngineError
        assert "blocked" in str(exc)

    # New compiles skip the blocked concept entirely.
    drafts = await store.compile_extraction("t2", "rag hallucinations", extraction, [])
    assert drafts == []

    # Unblock restores re-extraction.
    store.unblock_draft("never-approved")
    assert store.blocked_slugs() == []
    drafts = await store.compile_extraction("t3", "rag hallucinations", extraction, [])
    assert len(drafts) == 1


async def test_rejection_counter_below_block_still_approvable(tmp_path: Path) -> None:
    """Fewer than 5 rejections never trip the block."""
    from knowledge_engine.infrastructure.wiki import FileWikiStore
    from knowledge_engine.schemas.wiki import WikiExtraction

    store = FileWikiStore(root=tmp_path / "wiki")
    extraction = WikiExtraction(concepts=["Fine Concept"], entities=[], summary="x")
    for i in range(2):
        await store.compile_extraction("t1", "rag hallucinations", extraction, [])
        store.reject_draft("fine-concept", f"rejection {i}", query="rag hallucinations")
    assert store.blocked_slugs() == []
    await store.compile_extraction("t1", "rag hallucinations", extraction, [])
    assert (tmp_path / "wiki" / "drafts" / "fine-concept.md").is_file()


async def test_thesis_page_content(tmp_path: Path) -> None:
    """file_thesis writes claim, for/against, verdict, and a log entry."""
    from knowledge_engine.infrastructure.wiki import FileWikiStore

    store = FileWikiStore(root=tmp_path / "wiki")
    from knowledge_engine.schemas.wiki import WikiThesis

    page = await store.file_thesis(
        "t1",
        WikiThesis(
            claim="RAG reduces hallucinations",
            for_claims=["grounding", "citations"],
            against_claims=["retrieval noise"],
            verdict="supported with caveats",
            sources=["rag"],
        ),
    )
    text = Path(page).read_text(encoding="utf-8")
    assert "RAG reduces hallucinations" in text
    assert "grounding" in text and "retrieval noise" in text
    assert "supported with caveats" in text
    log = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "thesis" in log

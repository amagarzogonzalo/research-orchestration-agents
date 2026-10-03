"""Unit tests: synthesize evidence carries citable source IDs (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode, _labeled_body
from knowledge_engine.schemas.source import DocumentBody, FetchMethod, SearchSource, SourceRecord


def _src() -> SourceRecord:
    return SourceRecord(
        id="openalex:W123",
        title="Paper",
        source=SearchSource.OPENALEX,
        pdf_url="https://example.org/paper.pdf",
        doi="10.0000/x",
    )


def _body() -> DocumentBody:
    return DocumentBody(
        url="https://example.org/paper.pdf",
        title="Paper",
        markdown="Some findings.",
        crawl_method=FetchMethod.TRAFILATURA,
    )


def test_labeled_body_prefixes_matching_source_id() -> None:
    out = _labeled_body(_body(), {"sources": [_src()]})  # type: ignore[dict-item]
    assert out.startswith("[openalex:W123]\n")


def test_labeled_body_leaves_unmatched_bodies_plain() -> None:
    out = _labeled_body(_body(), {"sources": []})  # type: ignore[dict-item]
    assert out.startswith("Paper\n")


def test_build_prompt_lists_source_ids() -> None:
    prompt = SynthesizeNode._build_prompt({"query": "q", "sources": [_src()]}, None)  # type: ignore[dict-item]
    assert "[openalex:W123]" in prompt

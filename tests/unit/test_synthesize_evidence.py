"""Unit tests: synthesize evidence carries citable source IDs (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.synthesize import (
    SynthesizeNode,
    _cited_by_map,
    _labeled_body,
    _ledger_line,
    _norm_ref,
)
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


def test_build_prompt_is_stable_regardless_of_bodies_order() -> None:
    """Fan-out completion order must not change the prompt (cassette keys)."""
    first = DocumentBody(url="https://b.example/x", title="B", markdown="m", crawl_method=FetchMethod.TRAFILATURA)
    second = DocumentBody(url="https://a.example/x", title="A", markdown="m", crawl_method=FetchMethod.TRAFILATURA)
    state_fwd: dict = {"query": "q", "sources": [], "bodies": [first, second]}
    state_rev: dict = {"query": "q", "sources": [], "bodies": [second, first]}
    assert (
        SynthesizeNode._build_prompt(state_fwd, None)  # type: ignore[arg-type]
        == SynthesizeNode._build_prompt(state_rev, None)  # type: ignore[arg-type]
    )


def test_labeled_body_leaves_unmatched_bodies_plain() -> None:
    out = _labeled_body(_body(), {"sources": []})  # type: ignore[dict-item]
    assert out.startswith("Paper\n")


def test_build_prompt_lists_source_ids() -> None:
    prompt = SynthesizeNode._build_prompt({"query": "q", "sources": [_src()]}, None)  # type: ignore[dict-item]
    assert "[openalex:W123]" in prompt


def test_norm_ref_strips_doi_prefixes() -> None:
    assert _norm_ref("https://doi.org/10.1038/X") == "10.1038/x"
    assert _norm_ref("doi:10.1038/X") == "10.1038/x"
    assert _norm_ref("openalex:W123") == "openalex:w123"


def test_cited_by_map_links_doi_and_openalex_ids() -> None:
    citing = SourceRecord(
        id="openalex:W2",
        title="Citing",
        source=SearchSource.OPENALEX,
        references=["openalex:W1", "10.1038/x"],
    )
    cited = SourceRecord(id="openalex:W1", title="Cited", source=SearchSource.OPENALEX, doi="10.1038/x")
    assert _cited_by_map([citing, cited]) == {"openalex:w1": ["openalex:W2"], "10.1038/x": ["openalex:W2"]}


def test_cited_by_map_ignores_self_citation() -> None:
    src = SourceRecord(id="openalex:W1", title="t", source=SearchSource.OPENALEX, references=["openalex:W1"])
    assert _cited_by_map([src]) == {}


def test_ledger_line_shows_venue_and_citations() -> None:
    src = SourceRecord(id="openalex:W1", title="t", source=SearchSource.OPENALEX, venue="Nature", year=2024)
    line = _ledger_line(src, ["openalex:W2"])
    assert line.startswith("[openalex:W1] t — Nature, 2024 | cited in-set by: openalex:W2")


def test_ledger_line_without_meta_stays_short() -> None:
    src = SourceRecord(id="openalex:W1", title="t", source=SearchSource.OPENALEX)
    assert _ledger_line(src, []) == "[openalex:W1] t\n"

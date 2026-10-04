"""Unit tests: render completes dangling footnote definitions from provenance."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.render import _complete_footnotes
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(
    id: str,
    title: str,
    authors: list[str] | None = None,
    venue: str | None = None,
    year: int | None = None,
    doi: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        id=id, title=title, source=SearchSource.ARXIV, authors=authors or [], venue=venue, year=year, doi=doi
    )


def test_missing_definition_appended_from_provenance() -> None:
    notes = "Claim text [^openalex:W1]."
    out = _complete_footnotes(notes, [_src("openalex:W1", "Some Paper")])
    assert "[^openalex:W1]: Some Paper" in out
    assert out.startswith("Claim text")


def test_existing_definitions_untouched() -> None:
    notes = "Claim [^openalex:W1].\n\n[^openalex:W1]: Custom Title"
    assert _complete_footnotes(notes, [_src("openalex:W1", "Other")]) == notes


def test_unknown_ids_left_alone() -> None:
    notes = "Claim [^madeup:1]."
    assert _complete_footnotes(notes, [_src("openalex:W1", "Some Paper")]) == notes


def test_no_markers_returns_notes_unchanged() -> None:
    assert _complete_footnotes("Plain text.", []) == "Plain text."


def test_shortened_ids_resolve_to_provenance() -> None:
    notes = "Claim [^arx-2412-11919] and [^pn1ra-vpc]."
    prov = [_src("huggingface_papers:2412.11919", "RetroLLM"), _src("crossref:10.59350/pn1ra-vpc", "Vision")]
    out = _complete_footnotes(notes, prov)
    assert "[^arx-2412-11919]: RetroLLM" in out
    assert "[^pn1ra-vpc]: Vision" in out


def test_unresolvable_ids_left_alone() -> None:
    notes = "Claim [^zzz-no-such-9]."
    assert _complete_footnotes(notes, [_src("openalex:W1", "Some Paper")]) == notes


def test_doubled_prefix_markers_resolve() -> None:
    notes = "Claim [^crossref:10.1/10.1/x]."
    prov = [_src("crossref:10.1/x", "Paper")]
    out = _complete_footnotes(notes, prov)
    assert "[^crossref:10.1/10.1/x]: Paper" in out


def test_multi_cite_brackets_split() -> None:
    from knowledge_engine.orchestration.nodes.render import _normalize_citations

    prov = [_src("openalex:W1", "A"), _src("openalex:W2", "B")]
    out = _normalize_citations("Claim [^openalex:W1, openalex:W2].", prov)
    assert "[^openalex:W1] [^openalex:W2]" in out
    assert "[^openalex:W1]:" in out and "[^openalex:W2]:" in out


def test_multi_cite_with_repeated_carets_split() -> None:
    from knowledge_engine.orchestration.nodes.render import _normalize_citations

    prov = [_src("openalex:W1", "A"), _src("openalex:W2", "B")]
    out = _normalize_citations("Claim [^openalex:W1, ^openalex:W2].", prov)
    assert "[^openalex:W1] [^openalex:W2]" in out
    assert "[^openalex:W1]:" in out and "[^openalex:W2]:" in out


def test_plain_bracket_cites_normalize_to_footnotes() -> None:
    from knowledge_engine.orchestration.nodes.render import _normalize_citations

    prov = [_src("openalex:W1", "Some Paper", doi="10.1/x")]
    out = _normalize_citations("Claim [openalex:W1] and [10.1/x] plus [2024].", prov)
    assert "[^openalex:W1]" in out
    assert "[2024]" in out  # not a source id: untouched
    assert out.count("[^openalex:W1]:") == 1


def test_definitions_carry_full_citation() -> None:
    notes = "Claim [^openalex:W1]."
    prov = [
        _src("openalex:W1", "Some Paper", authors=["A. Uthor", "B. Writer"], venue="Nature", year=2024, doi="10.1/x")
    ]
    out = _complete_footnotes(notes, prov)
    assert "[^openalex:W1]: Some Paper — A. Uthor, B. Writer — (Nature, 2024)" in out
    assert "https://doi.org/10.1/x" in out

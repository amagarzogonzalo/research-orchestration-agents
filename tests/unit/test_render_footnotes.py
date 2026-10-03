"""Unit tests: render completes dangling footnote definitions from provenance."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.render import _complete_footnotes
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(id: str, title: str) -> SourceRecord:
    return SourceRecord(id=id, title=title, source=SearchSource.ARXIV)


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

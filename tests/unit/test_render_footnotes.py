"""Unit tests: footnote completion remaps dropped-duplicate IDs (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.render import (
    _cited_ids,
    _normalize_citations,
    _resolve_marker,
    _title_from,
    _tldr,
)
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(sid: str, title: str = "Paper") -> SourceRecord:
    return SourceRecord(id=sid, title=title, source=SearchSource.ARXIV)


def test_resolve_duckduckgo_url_id_to_arxiv_survivor() -> None:
    """A DDG record merged away as an arXiv duplicate still resolves."""
    titles = {"arxiv:2504.06214": "Long-context fine-tuning"}
    marker = "duckduckgo:https://arxiv.org/html/2504.06214v1"
    assert _resolve_marker(marker, titles) == "arxiv:2504.06214"


def test_resolve_huggingface_duplicate_to_arxiv_survivor() -> None:
    """An HF record merged away as an arXiv duplicate still resolves."""
    titles = {"arxiv:2309.00071": "YaRN"}
    assert _resolve_marker("huggingface_papers:2309.00071", titles) == "arxiv:2309.00071"


def test_resolve_unknown_stays_unknown() -> None:
    """Truly unretrieved IDs are left untouched (honest gap, not a fake cite)."""
    assert _resolve_marker("osf:zzzzzz_v9", {"arxiv:1": "t"}) is None


def test_normalize_citations_rewrites_dedup_variants() -> None:
    """Dangling markers for merged-away duplicates gain survivor definitions."""
    provenance = [_src("arxiv:2504.06214", "Long-context fine-tuning")]
    notes = "Claim text [^duckduckgo:https://arxiv.org/html/2504.06214v1]."
    out = _normalize_citations(notes, provenance)
    assert "[^arxiv:2504.06214]:" in out


def test_web_only_citation_def_carries_resolvable_url() -> None:
    """A general-web source without DOI/PDF gets its URL in the def line."""
    from knowledge_engine.orchestration.nodes.render import _full_citation

    rec = SourceRecord(id="duckduckgo:https://ex.io/rag", title="RAG blog", source=SearchSource.DUCKDUCKGO)
    assert _full_citation([rec], "duckduckgo:https://ex.io/rag").endswith("https://ex.io/rag")


def test_wikipedia_citation_def_resolves_curid() -> None:
    """Wikipedia page ids resolve to a canonical curid URL."""
    from knowledge_engine.orchestration.nodes.render import _full_citation

    rec = SourceRecord(id="wikipedia:59990826", title="RAG", source=SearchSource.WIKIPEDIA)
    assert "curid=59990826" in _full_citation([rec], "wikipedia:59990826")


def test_doubled_provider_prefix_collapsed() -> None:
    """A marker with a doubled provider prefix still resolves (audit defect)."""
    rec = SourceRecord(
        id="tavily:https://www.si.edu/leaves",
        title="Why leaves change color",
        source=SearchSource.TAVILY,
    )
    notes = "Claim [^tavily:https://tavily:https://www.si.edu/leaves]."
    out = _normalize_citations(notes, [rec])
    assert "[^tavily:https://tavily:https://" not in out
    assert "[^tavily:https://www.si.edu/leaves]:" in out


def test_bare_multi_cite_split_into_markers() -> None:
    """Comma-joined bare citations become one marker per source (audit defect)."""
    recs = [
        SourceRecord(id="tavily:https://a.io/x", title="A", source=SearchSource.TAVILY),
        SourceRecord(id="tavily:https://b.io/y", title="B", source=SearchSource.TAVILY),
    ]
    notes = "Claim [tavily:https://a.io/x, tavily:https://b.io/y]."
    out = _normalize_citations(notes, recs)
    assert "[^tavily:https://a.io/x] [^tavily:https://b.io/y]" in out
    assert "[^tavily:https://a.io/x]:" in out
    assert "[^tavily:https://b.io/y]:" in out


def test_truncated_definition_is_regenerated() -> None:
    """A definition truncated mid-line is rebuilt from provenance (audit defect)."""
    rec = _src("arxiv:2504.06214", "Long-context fine-tuning")
    notes = "Claim [^arxiv:2504.06214].\n\n[^arxiv:2504.06214]: Long-context fine-tun"
    out = _normalize_citations(notes, [rec])
    assert "fine-tun\n" not in out
    assert "[^arxiv:2504.06214]: Long-context fine-tuning" in out


def test_unreferenced_definitions_are_pruned() -> None:
    """Definitions for sources the model never cites are dropped (audit defect)."""
    rec = _src("arxiv:2504.06214", "Long-context fine-tuning")
    notes = "Body only.\n\n[^arxiv:2504.06214]: Long-context fine-tuning"
    out = _normalize_citations(notes, [rec])
    assert "[^arxiv:2504.06214]" not in out
    assert out.strip() == "Body only."


def test_unknown_marker_is_dropped() -> None:
    """Markers for never-retrieved ids are removed, not left dangling."""
    rec = _src("arxiv:2504.06214", "Long-context fine-tuning")
    notes = "Claim [^osf:zzzzzz_v9]."
    assert "[^osf" not in _normalize_citations(notes, [rec])


def test_cited_ids_follow_marker_order() -> None:
    """The artifact citation list only carries cited sources."""
    recs = [_src("arxiv:1", "One"), _src("arxiv:2", "Two")]
    notes = "Text [^arxiv:2] then [^arxiv:1]."
    assert _cited_ids(notes, recs) == ["arxiv:2", "arxiv:1"]


def test_title_preserves_trailing_question_mark() -> None:
    """Titles keep the question mark (audit defect: '...?' was stripped)."""
    assert _title_from("Why do leaves change color?") == "Why do leaves change color?"


def test_tldr_skips_headings_and_cuts_on_word_boundary() -> None:
    """The tldr starts at prose and never chops a word mid-word (audit defect)."""
    notes = "## Core Definition\n\n" + " ".join(["word"] * 80) + "."
    out = _tldr(notes)
    assert not out.startswith("##")
    assert len(out) <= 200
    assert not out.endswith("wor")

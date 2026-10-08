"""Unit tests: footnote completion remaps dropped-duplicate IDs (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.render import _complete_footnotes, _resolve_marker
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


def test_complete_footnotes_rewrites_dedup_variants() -> None:
    """Dangling markers for merged-away duplicates gain survivor definitions."""
    provenance = [_src("arxiv:2504.06214", "Long-context fine-tuning")]
    notes = "Claim text [^duckduckgo:https://arxiv.org/html/2504.06214v1]."
    out = _complete_footnotes(notes, provenance)
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

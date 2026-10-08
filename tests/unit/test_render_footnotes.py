"""Unit tests: footnote completion remaps dropped-duplicate IDs (offline)."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.render import _complete_footnotes, _full_citation, _resolve_marker
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


def test_full_citation_carries_url_for_web_source() -> None:
    """General-web records have no DOI/PDF; the footnote must carry the URL."""
    provenance = [_src("tavily:https://example.io/free-tiers", "Free cloud tiers for agents")]
    line = _full_citation(provenance, "tavily:https://example.io/free-tiers")
    assert "https://example.io/free-tiers" in line
    assert line.startswith("Free cloud tiers for agents")

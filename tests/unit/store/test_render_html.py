"""Unit tests: render_article_html — the click-to-read surface (ADR-0012 + PR #28 follow-up).

The converter handles EXACTLY the synthesis output contract (headings, bullet
lists, bold, inline code, footnote markers + definitions) and nothing more —
deterministic, zero-dependency, escaped-by-default.
"""

from __future__ import annotations

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SearchSource, SourceRecord
from knowledge_engine.store.render_html import render_article_html


def _artifact(body: str, provenance: list[SourceRecord] | None = None) -> KnowledgeArtifact:
    """Build an artifact with frontmatter + body.

    Args:
        body: Markdown body.
        provenance: Source records for footnote link resolution.

    Returns:
        KnowledgeArtifact: Test artifact.
    """
    return KnowledgeArtifact(
        id="t1",
        title="Test Article",
        body_markdown=body,
        frontmatter_yaml="title: Test Article\n",
        citations=[s.id for s in (provenance or [])],
        provenance=provenance or [],
        generated_at="2026-10-08T00:00:00+00:00",
    )


def _rec(sid: str, title: str, source: SearchSource, doi: str | None = None, pdf: str | None = None) -> SourceRecord:
    """Build a source record.

    Args:
        sid: Source id.
        title: Title.
        source: Source kind.
        doi: Optional DOI.
        pdf: Optional pdf url.

    Returns:
        SourceRecord: Test record.
    """
    return SourceRecord(id=sid, title=title, source=source, doi=doi, pdf_url=pdf)


def test_escapes_html_by_default() -> None:
    """Angle brackets in source text never become markup."""
    html = render_article_html(_artifact("Claim <script>alert(1)</script> text"))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_headings_and_paragraphs() -> None:
    """#/##/### become h1/h2/h3; blank-line groups become paragraphs."""
    html = render_article_html(_artifact("# Title\n\nPara one.\n\n## Section\n\nPara two."))
    assert "<h1>Title</h1>" in html
    assert "<h2>Section</h2>" in html
    assert "<p>Para one.</p>" in html
    assert "<p>Para two.</p>" in html


def test_footnote_marker_becomes_anchor() -> None:
    """[^id] markers link to their definition anchor."""
    body = "Grounded claim [^openalex:W1].\n\n[^openalex:W1]: Paper One — evidence."
    html = render_article_html(_artifact(body))
    assert 'href="#fn-openalex-w1"' in html
    assert "<sup>" in html


def test_footnote_definition_links_to_source_url() -> None:
    """Definitions anchor + carry a real resolvable source link (doi path)."""
    rec = _rec("openalex:W1", "Paper One", SearchSource.OPENALEX, doi="10.1000/x")
    body = "Claim [^openalex:W1].\n\n[^openalex:W1]: Paper One — evidence."
    html = render_article_html(_artifact(body, [rec]))
    assert 'id="fn-openalex-w1"' in html
    assert 'href="https://doi.org/10.1000/x"' in html


def test_web_source_definition_links_to_embedded_url() -> None:
    """General-web records resolve via the URL in their canonical id."""
    rec = _rec("duckduckgo:https://ex.io/rag", "RAG blog", SearchSource.DUCKDUCKGO)
    body = "Claim [^duckduckgo:https://ex.io/rag].\n\n[^duckduckgo:https://ex.io/rag]: RAG blog."
    html = render_article_html(_artifact(body, [rec]))
    assert 'href="https://ex.io/rag"' in html


def test_bullets_and_inline_styles() -> None:
    """- lists become ul/li; **bold** and `code` render."""
    body = "- one **bold** point\n- two `code` point"
    html = render_article_html(_artifact(body))
    assert "<ul>" in html and "<li>" in html
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html


def test_document_is_self_contained() -> None:
    """Output is a full standalone HTML document with inline styles."""
    html = render_article_html(_artifact("# T\n\nbody"))
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "Test Article" in html


def test_marker_without_def_stays_plain_text() -> None:
    """A marker without a definition renders as plain text (honest gap)."""
    body = "Claim [^unknown:xyz] with no def line."
    html = render_article_html(_artifact(body))
    assert "[^unknown:xyz]" in html

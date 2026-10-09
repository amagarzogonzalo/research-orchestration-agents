"""render_article_html — self-contained click-to-read HTML for published artifacts.

Renders EXACTLY the synthesis output contract (ADR-0008 node formats):
``#``/``##``/``###`` headings, blank-line paragraphs, ``- `` bullets, ``**bold**``,
`` `code` ``, footnote markers ``[^id]`` -> superscript anchors, footnote
definitions ``[^id]: text`` -> anchored list items carrying a REAL source link
(doi.org / pdf_url / the URL embedded in general-web canonical ids). Everything
is HTML-escaped BEFORE transforms, so model text can never inject markup.

Zero dependencies by design (repo rule 4): this is a focused renderer for our
own constrained output, not a general markdown parser.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from knowledge_engine.schemas.artifact import KnowledgeArtifact
from knowledge_engine.schemas.source import SourceRecord

_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_.:/-]+)\]")
_FOOTNOTE_DEF = re.compile(r"(?m)^\[\^([A-Za-z0-9_.:/-]+)\]:\s*(.*)$")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*-\s+(.*)$")

_CSS = (
    "body{font:16px/1.6 Georgia,serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#1a1a2e}"
    "h1,h2,h3{font-family:-apple-system,Segoe UI,sans-serif;line-height:1.25;color:#16213e}"
    "sup a{text-decoration:none}"
    ".footnotes{border-top:1px solid #ccc;margin-top:2rem;padding-top:.5rem;font-size:.9em}"
    ".footnotes li{margin:.35rem 0}"
    "blockquote{border-left:3px solid #ccc;margin-left:0;padding-left:1rem;color:#555}"
)


def _anchor_id(marker: str) -> str:
    """URL-safe anchor for a footnote marker.

    Args:
        marker: Raw marker id.

    Returns:
        str: ``fn-`` + lowercased alphanum/dash id.
    """
    return "fn-" + re.sub(r"[^a-z0-9-]", "-", marker.lower()).strip("-")


def _source_url(rec: SourceRecord) -> str | None:
    """Resolve the best external locator for a provenance record.

    Args:
        rec: Source record.

    Returns:
        str | None: Resolvable URL or None.
    """
    if rec.pdf_url:
        return rec.pdf_url
    if rec.doi:
        return f"https://doi.org/{rec.doi}"
    if rec.id.startswith(("tavily:", "exa:", "duckduckgo:", "serper:")):
        url = rec.id.partition(":")[2]
        return url if url.startswith("http") else None
    if rec.id.startswith("wikipedia:"):
        pageid = rec.id.partition(":")[2]
        return f"https://en.wikipedia.org/wiki/?curid={pageid}" if pageid else None
    return None


def _inline(text: str, known: set[str]) -> str:
    """Render inline markdown (bold/code/markers) on pre-escaped text.

    Args:
        text: Escaped text.
        known: Footnote ids that have definitions (anchors exist).

    Returns:
        str: Inline HTML.
    """
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return _FOOTNOTE_REF.sub(lambda m: _marker_html(m.group(1), known), text)


def _marker_html(marker: str, known: set[str]) -> str:
    """Render one footnote marker.

    Args:
        marker: Raw marker id.
        known: Defined footnote ids.

    Returns:
        str: Superscript anchor when defined, else the plain marker.
    """
    if marker not in known:
        return f"[^{marker}]"
    return f'<sup><a href="#{_anchor_id(marker)}">{html.escape(marker)}</a></sup>'


def render_article_html(artifact: KnowledgeArtifact) -> str:
    """Render a KnowledgeArtifact as a standalone, styled HTML document.

    Args:
        artifact: The published artifact (body markdown + provenance).

    Returns:
        str: Complete ``<!DOCTYPE html>`` document; footnotes carry real
            source links via the artifact's provenance.
    """
    provenance: Sequence[SourceRecord] = artifact.provenance
    urls = {rec.id: _source_url(rec) for rec in provenance}

    body_text = artifact.body_markdown
    defs: list[tuple[str, str]] = [(m, t.strip()) for m, t in _FOOTNOTE_DEF.findall(body_text)]
    known = {marker for marker, _text in defs}

    escaped = html.escape(body_text, quote=False)
    blocks: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph), known)}</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            blocks.append("<ul>" + "".join(f"<li>{_inline(b, known)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw_line in escaped.splitlines():
        line = raw_line.rstrip()
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if _FOOTNOTE_DEF.match(line):
            flush_paragraph()
            flush_bullets()
            continue  # definitions render in the footnotes section, not inline
        if heading:
            flush_paragraph()
            flush_bullets()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline(heading.group(2).strip(), known)}</h{level}>")
        elif bullet:
            flush_paragraph()
            bullets.append(bullet.group(1))
        elif not line.strip():
            flush_paragraph()
            flush_bullets()
        else:
            paragraph.append(line.strip())
    flush_paragraph()
    flush_bullets()

    if defs:
        items = []
        for marker, text in defs:
            url = urls.get(marker)
            link = f' <a href="{html.escape(url, quote=True)}">&#8599;</a>' if url else ""
            items.append(f'<li id="{_anchor_id(marker)}">{_inline(html.escape(text, quote=False), known)}{link}</li>')
        blocks.append('<section class="footnotes"><ol>' + "".join(items) + "</ol></section>")

    title = html.escape(artifact.title or artifact.id)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        f"<h1>{title}</h1>{''.join(blocks)}"
        "</body></html>\n"
    )


__all__ = ["render_article_html"]

"""Unit tests: bibliography stripping keeps evidence, drops DOI lists."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.bodies import _strip_references


def test_trailing_references_cut() -> None:
    prose = "Findings text here with plenty of evidence. " * 10
    body = f"{prose}\n\n## References\n\n1. Author (2024). doi:10.1/x\n2. Other (2023).\n"
    out = _strip_references(body)
    assert "Findings text here with plenty of evidence." in out
    assert "doi:10.1/x" not in out


def test_no_heading_returns_unchanged() -> None:
    body = "Plain findings without bibliography."
    assert _strip_references(body) == body


def test_early_heading_never_guts_body() -> None:
    body = "## References\n\nShort note."
    assert _strip_references(body) == body

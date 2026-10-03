"""Unit tests: _derive_url prefers scrape-friendly HTML landing pages."""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.bodies import _derive_url
from knowledge_engine.schemas.source import SearchSource, SourceRecord


def _src(id: str, pdf_url: str | None = None, doi: str | None = None) -> SourceRecord:
    return SourceRecord(id=id, title="t", source=SearchSource.ARXIV, pdf_url=pdf_url, doi=doi)


def test_huggingface_papers_resolve_to_arxiv_abs() -> None:
    assert _derive_url(_src("huggingface_papers:2412.11919")) == "https://arxiv.org/abs/2412.11919"


def test_arxiv_resolves_to_abs() -> None:
    assert _derive_url(_src("arxiv:2412.11919")) == "https://arxiv.org/abs/2412.11919"


def test_doi_falls_back_to_resolver() -> None:
    assert _derive_url(_src("crossref:10.1038/nature12373", doi="10.1038/nature12373")) == (
        "https://doi.org/10.1038/nature12373"
    )


def test_no_url_returns_none() -> None:
    assert _derive_url(_src("doaj:abc")) is None

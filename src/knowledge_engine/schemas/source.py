"""Federated scholarly-search + scraped-content models (ADR 0002 §3–§4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SearchSource(StrEnum):
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    CROSSREF = "crossref"
    ARXIV = "arxiv"


class SourceRecord(BaseModel):
    """Normalized scholarly hit; the single cross-provider search output."""

    model_config = ConfigDict(extra="forbid")

    id: str  # canonical id, e.g. "openalex:W7163824001"
    doi: str | None = None
    title: str
    source: SearchSource
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    pdf_url: str | None = None
    citation_count: int = 0
    tldr_llm: str | None = None  # ~50-token summary for downstream filtering
    fetched_at: str | None = None


class FetchMethod(StrEnum):
    CRAWL4AI = "crawl4ai"
    TRAFILATURA = "trafilatura"


class DocumentBody(BaseModel):
    """LLM-ready Markdown extracted from a URL (token-reduced, heuristic-chunked)."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    markdown: str = ""
    char_count: int = 0
    crawl_method: FetchMethod = FetchMethod.CRAWL4AI
    captured_at: str | None = None
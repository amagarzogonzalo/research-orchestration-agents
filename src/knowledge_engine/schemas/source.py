"""Federated scholarly-search + scraped-content models (ADR 0002 §3–§4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SearchSource(StrEnum):
    """SearchSource.

    Attributes:
        OPENALEX: Attribute description.
        SEMANTIC_SCHOLAR: Attribute description.
        CROSSREF: Attribute description.
        ARXIV: Attribute description.
        HUGGINGFACE_PAPERS: Attribute description.
        PUBMED: Attribute description.
        BIORXIV: Attribute description.
        MEDRXIV: Attribute description.
        EUROPE_PMC: Attribute description.
        UNPAYWALL: Attribute description.
        DBLP: Attribute description.
        DOAJ: Attribute description.
        OSF_PREPRINTS: Attribute description.
        WIKIPEDIA: Attribute description.
        DUCKDUCKGO: Attribute description.
        TAVILY: Attribute description.
        EXA: Attribute description.
        SERPER: Attribute description."""

    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    CROSSREF = "crossref"
    ARXIV = "arxiv"
    HUGGINGFACE_PAPERS = "huggingface_papers"
    PUBMED = "pubmed"
    BIORXIV = "biorxiv"
    MEDRXIV = "medrxiv"
    EUROPE_PMC = "europe_pmc"
    UNPAYWALL = "unpaywall"
    DBLP = "dblp"
    DOAJ = "doaj"
    OSF_PREPRINTS = "osf_preprints"
    WIKIPEDIA = "wikipedia"
    DUCKDUCKGO = "duckduckgo"
    TAVILY = "tavily"
    EXA = "exa"
    SERPER = "serper"


class SourceRecord(BaseModel):
    """Normalized scholarly hit; the single cross-provider search output.

    Attributes:
        model_config: Attribute description.
        id: Attribute description.
        doi: Attribute description.
        title: Attribute description.
        source: Attribute description.
        authors: Attribute description.
        year: Attribute description.
        abstract: Attribute description.
        pdf_url: Attribute description.
        citation_count: Attribute description.
        venue: Attribute description.
        references: Attribute description.
        tldr_llm: Attribute description.
        fetched_at: Attribute description.
    """

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
    venue: str | None = None  # journal / conference / server, when the API returns it
    references: list[str] = Field(default_factory=list)  # cited IDs (DOIs or canonical ids)
    tldr_llm: str | None = None  # ~50-token summary for downstream filtering
    fetched_at: str | None = None


class FetchMethod(StrEnum):
    """FetchMethod.

    Attributes:
        CRAWL4AI: Attribute description.
        TRAFILATURA: Attribute description.
        PLAYWRIGHT: Attribute description.
        JINA: Attribute description.
        API: Attribute description."""

    CRAWL4AI = "crawl4ai"
    TRAFILATURA = "trafilatura"
    PLAYWRIGHT = "playwright"
    JINA = "jina"
    API = "api"


class DocumentBody(BaseModel):
    """LLM-ready Markdown extracted from a URL (token-reduced, heuristic-chunked).

    Attributes:
        model_config: Attribute description.
        url: Attribute description.
        title: Attribute description.
        markdown: Attribute description.
        char_count: Attribute description.
        crawl_method: Attribute description.
        captured_at: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    markdown: str = ""
    char_count: int = 0
    crawl_method: FetchMethod = FetchMethod.CRAWL4AI
    captured_at: str | None = None


class BodyRef(BaseModel):
    """Lightweight pointer to a persisted body (ADR 0010, state v2).

    Checkpoints copy the whole state at every superstep, so channels carry
    ~150-byte refs instead of 10-50KB markdown blobs. ``url`` is retained so
    the synthesize URL->source-id reverse map keeps working without loading.

    Attributes:
        model_config: Attribute description.
        url: Attribute description.
        title: Attribute description.
        path: Attribute description.
        char_count: Attribute description.
        crawl_method: Attribute description.
        captured_at: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    path: str
    char_count: int = 0
    crawl_method: FetchMethod = FetchMethod.CRAWL4AI
    captured_at: str | None = None

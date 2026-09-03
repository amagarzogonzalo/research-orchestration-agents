"""Standardized knowledge-base artifact for downstream RAG (ADR 0002 §7)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from knowledge_engine.schemas.source import SourceRecord


class KnowledgeArtifact(BaseModel):
    """Schema.org-aligned Markdown artifact with rigorous YAML frontmatter."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str = "KnowledgeBaseArticle"
    title: str
    provenance: list[SourceRecord] = Field(default_factory=list)
    body_markdown: str = ""
    frontmatter_yaml: str = ""
    citations: list[str] = Field(default_factory=list)
    tldr_llm: str = ""  # ~50 words so future RAG can filter without loading the body
    generated_at: str = ""
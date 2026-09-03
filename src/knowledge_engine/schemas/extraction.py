"""Extraction-task + orchestration-state models (ADR 0002 §5–§6)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from knowledge_engine.schemas.source import DocumentBody, SourceRecord


class ExtractionKind(StrEnum):
    BOOL_FILTER = "bool_filter"
    ENTITY_EXTRACT = "entity_extract"
    SUMMARY_50 = "summary_50"
    SYNTHETIC_MULTI_DOC = "synthetic_multi_doc"


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    kind: ExtractionKind
    source: SourceRecord | DocumentBody
    schema_ref: str  # dotted path to the target Pydantic model for Instructor
    retries: int = Field(default=2, ge=0)


class GraphStatus(StrEnum):
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    DONE = "done"
    FAILED = "failed"


class GraphState(BaseModel):
    """Durable state threaded through the LangGraph machine (ADR 0005)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    query: str
    sources: list[SourceRecord] = Field(default_factory=list)
    bodies: list[DocumentBody] = Field(default_factory=list)
    filtered: list[SourceRecord] = Field(default_factory=list)
    synthetic_notes: list[str] = Field(default_factory=list)
    judge_score: float | None = None
    judge_critique: str | None = None
    output_path: str | None = None
    status: GraphStatus = GraphStatus.RUNNING
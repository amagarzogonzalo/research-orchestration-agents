"""Extraction-task + orchestration-state models (ADR 0002 §5–§6)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from knowledge_engine.schemas.source import BodyRef, DocumentBody, SourceRecord


class ExtractionKind(StrEnum):
    """ExtractionKind.

    Attributes:
        BOOL_FILTER: Attribute description.
        ENTITY_EXTRACT: Attribute description.
        SUMMARY_50: Attribute description.
        SYNTHETIC_MULTI_DOC: Attribute description."""

    BOOL_FILTER = "bool_filter"
    ENTITY_EXTRACT = "entity_extract"
    SUMMARY_50 = "summary_50"
    SYNTHETIC_MULTI_DOC = "synthetic_multi_doc"


class ExtractionRequest(BaseModel):
    """ExtractionRequest.

    Attributes:
        model_config: Attribute description.
        task_id: Attribute description.
        kind: Attribute description.
        source: Attribute description.
        schema_ref: Attribute description.
        retries: Attribute description."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    kind: ExtractionKind
    source: SourceRecord | DocumentBody
    schema_ref: str  # dotted path to the target Pydantic model for Instructor
    retries: int = Field(default=2, ge=0)


class GraphStatus(StrEnum):
    """Lifecycle status of a research run.

    Attributes:
        RUNNING: Actively running.
        AWAITING_REVIEW: Paused for human review.
        DONE: Completed successfully.
        FAILED: Failed terminally.
    """

    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    DONE = "done"
    FAILED = "failed"


def _last_wins(current: Any, update: Any) -> Any:
    """Last-writer-wins reducer for concurrently written scalar channels.

    Parallel branches (and their error handlers) may write ``status`` and
    ``judge_critique`` in the same step; LastValue channels reject that with
    ``InvalidUpdateError`` and crash the run. Concurrent failure writes agree
    on the outcome, so keeping the latest is both safe and deterministic
    enough for a terminal signal.

    Args:
        current: Existing channel value (ignored).
        update: Incoming channel value.

    Returns:
        Any: The incoming value.
    """
    return update


MAX_SYNTHESIS_ITERATIONS = 2
"""Bound on judge→synthesize optimizer loops (evaluator-optimizer discipline)."""


class GraphState(BaseModel):
    """Durable state threaded through the LangGraph machine (ADR 0005).

    Attributes:
        model_config: Attribute description.
        thread_id: Attribute description.
        query: Attribute description.
        sources: Attribute description.
        bodies: Attribute description.
        filtered: Attribute description.
        synthetic_notes: Attribute description.
        judge_score: Attribute description.
        judge_critique: Attribute description.
        output_path: Attribute description.
        status: Attribute description.
        iterations: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    query: str
    sources: list[SourceRecord] = Field(default_factory=list)
    bodies: list[BodyRef | DocumentBody] = Field(default_factory=list)
    filtered: list[SourceRecord] = Field(default_factory=list)
    synthetic_notes: list[str] = Field(default_factory=list)
    judge_score: float | None = None
    judge_critique: Annotated[str | None, _last_wins] = None
    output_path: str | None = None
    status: Annotated[GraphStatus, _last_wins] = GraphStatus.RUNNING
    iterations: int = Field(default=0, ge=0)

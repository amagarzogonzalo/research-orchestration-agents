"""LangGraph workflow state + node base (SOLID node classes, ADR 0008).

The :class:`WorkflowState` is the raw, channel-based state LangGraph threads
through the graph. It deliberately stores raw data (typed records), not
formatted text: nodes format prompts on demand, keeping prompt templates
from ever breaking the state schema.

The Pydantic :class:`~knowledge_engine.schemas.extraction.GraphState` remains
the durable, serializable snapshot returned to callers and persisted by the
checkpointer.
"""

from __future__ import annotations

from collections.abc import Sequence
from operator import add
from typing import Annotated, Any, TypedDict

from langgraph.types import Command, Send

from knowledge_engine.schemas.extraction import MAX_SYNTHESIS_ITERATIONS, GraphState, GraphStatus
from knowledge_engine.schemas.source import DocumentBody, SourceRecord


class WorkflowState(TypedDict, total=False):
    """Raw typed channel state shared by all nodes/graphs.

        Accumulated channels use an ``add`` reducer so LangGraph appends rather
        than overwrites — required for the judge→synthesize optimizer loop and the
        ``Send`` fan-out back into ``bodies``.

    Attributes:
        thread_id: Attribute description.
        workflow: Attribute description.
        query: Attribute description.
        mode: Attribute description.
        intent: Attribute description.
        depth: Attribute description.
        rounds: Attribute description.
        llm_calls: Attribute description.
        sub_queries: Attribute description.
        branch_notes: Attribute description.
        rubric_score: Attribute description.
        gaps: Attribute description.
        sources: Attribute description.
        filtered: Attribute description.
        bodies: Attribute description.
        notes: Attribute description.
        judge_score: Attribute description.
        judge_critique: Attribute description.
        iterations: Attribute description.
        review_dir: Attribute description.
        output_path: Attribute description.
        status: Attribute description.
    """

    thread_id: str
    workflow: str
    query: str
    mode: str | None
    intent: str
    depth: int
    rounds: int
    llm_calls: Annotated[int, add]
    sub_queries: list[dict[str, Any]]
    branch_notes: Annotated[list[str], add]
    rubric_score: float | None
    gaps: list[str]
    sources: list[SourceRecord]
    filtered: list[SourceRecord]
    bodies: Annotated[list[DocumentBody], add]
    notes: Annotated[list[str], add]
    judge_score: float | None
    judge_critique: str | None
    iterations: int
    review_dir: str | None
    output_path: str | None
    status: str


def to_graph_state(state: WorkflowState) -> GraphState:
    """Materialize a durable :class:`GraphState` snapshot from channel state.

    Args:
        state: Current workflow state.

    Returns:
        GraphState: Durable state snapshot.

    """
    return GraphState(
        thread_id=state.get("thread_id", ""),
        query=state.get("query", ""),
        sources=state.get("sources", []),
        filtered=state.get("filtered", []),
        bodies=state.get("bodies", []),
        synthetic_notes=state.get("notes", []),
        judge_score=state.get("judge_score"),
        judge_critique=state.get("judge_critique"),
        output_path=state.get("output_path"),
        status=GraphStatus(state.get("status", GraphStatus.RUNNING.value)),
        iterations=state.get("iterations", 0),
    )


class WorkflowNode:
    """Base for dependency-injected, stateless-per-run LangGraph nodes.

    Concrete nodes implement :meth:`run`, are immutable (dependencies injected
    via ``__init__``), and hold no per-run state — keeping them pure,
    replay-safe, and unit-testable with fakes. Nodes write raw data; formatting
    happens inside the node against the current :class:`WorkflowState`.
    """

    __slots__ = ()

    async def run(self, state: WorkflowState) -> dict[str, Any] | Command[Any]:  # pragma: no cover - interface
        """Execute the node, returning the partial state writes or a Command.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any] | Command[Any]: State update or routing command.

        Raises:
            NotImplementedError: When the operation fails.

        """
        raise NotImplementedError

    async def __call__(self, state: WorkflowState) -> dict[str, Any] | Command[Any]:
        """Run the node.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any] | Command[Any]: State update or routing command.

        """
        return await self.run(state)

    def command(self, *, update: dict[str, Any] | None = None, goto: str | Sequence[Send] | Send) -> Command[Any]:
        """SOTA routing: combine a state update with an explicit next node.

        Args:
            update: State update mapping.
            goto: Next node routing.

        Returns:
            Command[Any]: Routing command.

        """
        return Command(update=update or {}, goto=goto)


__all__ = [
    "DocumentBody",
    "GraphStatus",
    "MAX_SYNTHESIS_ITERATIONS",
    "WorkflowState",
    "WorkflowNode",
    "to_graph_state",
]

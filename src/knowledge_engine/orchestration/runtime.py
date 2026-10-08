"""GraphRuntime — the single external surface (ADR 0008 §2).

One request in → one :class:`GraphState` out. Drives the compiled graph with a
``thread_id`` config, surfaces any durable ``interrupt()`` (HITL review gate)
to the caller, and resumes it with ``Command(resume=...)``. Workflow events
stay internal (Pregel); nothing is republished externally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from knowledge_engine.orchestration.graphs import NodeSet, build_workflow
from knowledge_engine.orchestration.state import to_graph_state
from knowledge_engine.schemas.extraction import GraphState


@dataclass(frozen=True)
class RunResult:
    """Outcome of a graph run: the durable snapshot + any pending interrupt.

    Attributes:
        state: Attribute description.
        interrupted: Attribute description.
        interrupt_payload: Attribute description.
    """

    state: GraphState
    interrupted: bool = False
    interrupt_payload: Any | None = None


@dataclass
class GraphRuntime:
    """Composes + runs compiled graphs; owns resume semantics per thread.

    Attributes:
        nodes: Attribute description.
        checkpointer: Attribute description.
        store: Attribute description.
    """

    nodes: NodeSet
    checkpointer: BaseCheckpointSaver[Any] | None = None
    store: Any | None = None
    _graphs: dict[str, CompiledStateGraph[Any, Any, Any, Any]] = field(default_factory=dict, init=False)

    def graph(self, workflow: str) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Graph.

        Args:
            workflow: Workflow name to run.

        Returns:
            CompiledStateGraph[Any, Any, Any, Any]: Compiled graph.
        """
        if workflow not in self._graphs:
            self._graphs[workflow] = build_workflow(
                workflow, self.nodes, checkpointer=self.checkpointer, store=self.store
            )
        return self._graphs[workflow]

    def _config(self, thread_id: str) -> RunnableConfig:
        """Config.

        Args:
            thread_id: Run thread identifier.

        Returns:
            RunnableConfig: Runnable configuration.

        """
        return {"configurable": {"thread_id": thread_id}}

    async def run(self, workflow: str, thread_id: str, query: str, *, mode: str | None = None) -> RunResult:
        """Start (or resume-if-pending) one run; returns the graph outcome.

        Args:
            workflow: Workflow name to run.
            thread_id: Run thread identifier.
            query: Scholarly query string.
            mode: Explicit intent override (e.g. ``deep``) that beats the
                intent heuristic — an explicitly requested workflow must not
                be fast-pathed away by it.

        Returns:
            RunResult: Run result.

        """
        graph = self.graph(workflow)
        initial: dict[str, Any] = {
            "thread_id": thread_id,
            "workflow": workflow,
            "query": query,
            "status": "running",
        }
        if mode:
            initial["mode"] = mode
        result = await graph.ainvoke(initial, config=self._config(thread_id))
        return _to_result(result)

    async def resume(self, workflow: str, thread_id: str, payload: Any) -> RunResult:
        """Resume a run paused at a durable ``interrupt()`` with a human reply.

        Args:
            workflow: Workflow name to run.
            thread_id: Run thread identifier.
            payload: Event or resume payload.

        Returns:
            RunResult: Run result.

        """
        graph = self.graph(workflow)
        result = await graph.ainvoke(Command(resume=payload), config=self._config(thread_id))
        return _to_result(result)

    async def get_state(self, workflow: str, thread_id: str) -> RunResult:
        """Get state.

        Args:
            workflow: Workflow name to run.
            thread_id: Run thread identifier.

        Returns:
            RunResult: Run result.
        """
        snapshot = await self.graph(workflow).aget_state(self._config(thread_id))
        return _to_result(snapshot.values)

    async def delete_thread(self, thread_id: str) -> None:
        """Delete a thread's checkpoints (retention hook for one-shot runs).

        Call after the artifact is archived (``ResearchStore`` files or
        Supabase Storage) and HITL resume is no longer needed. Keeps the
        Postgres tables from growing one thread per query.

        Args:
            thread_id: Run thread identifier.
        """
        checkpointer = self.checkpointer
        if checkpointer is None:
            return
        adelete = getattr(checkpointer, "adelete_thread", None)
        if callable(adelete):
            await adelete(thread_id)
            return
        delete = getattr(checkpointer, "delete_thread", None)
        if callable(delete):
            delete(thread_id)


def _to_result(values: Any) -> RunResult:
    """Coerce a Pregel output dict (or snapshot) into a durable GraphState.

    Args:
        values: State values mapping.

    Returns:
        RunResult: Run result.

    """
    if values is None:
        return RunResult(state=to_graph_state({}))
    if isinstance(values, GraphState):
        return RunResult(state=values)
    interrupt = (values or {}).get("__interrupt__")
    state = to_graph_state(values)
    payload = None
    if isinstance(interrupt, (list, tuple)) and interrupt:
        payload = interrupt[0].value if hasattr(interrupt[0], "value") else interrupt[0]
    elif interrupt is not None:
        payload = interrupt
    return RunResult(state=state, interrupted=interrupt is not None, interrupt_payload=payload)


__all__ = ["GraphRuntime", "RunResult", "to_graph_state"]

"""agent node: create_agent with MCP tools for the agentic synthesis step.

:class:`AgentSynthesizeNode` is the SOTA agentic variant of ``synthesize``:
it builds a :func:`langchain.agents.create_agent` graph whose tool layer is
loaded from MCP servers (search/scrape) via :class:`langchain.mcp.MCPAdapter`,
then runs the model loop to produce notes.

Tool discovery is injected via a lazy ``agent_builder`` callable so the node
stays deterministic-testable without a live MCP server; when the loader/model
is unavailable, callers fall back to the port-based
:class:`~knowledge_engine.orchestration.nodes.synthesize.SynthesizeNode`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from knowledge_engine.orchestration.state import (
    MAX_SYNTHESIS_ITERATIONS,
    WorkflowNode,
    WorkflowState,
)
from knowledge_engine.schemas.provider import Affinity, ChatRequest
from knowledge_engine.schemas.source import DocumentBody

logger = logging.getLogger(__name__)

AgentBuilder = Callable[[], Awaitable[Any]]

_AGENT_SYSTEM = (
    "You are a senior research synthesizer. Use the MCP search/scrape tools to "
    "gather evidence, then produce authoritative, grounded notes that directly "
    "answer the research question."
)


class AgentSynthesizeNode(WorkflowNode):
    """Run a create_agent loop (MCP tools) over the query + state context.

    ``agent_builder`` returns an async callable yielding a compiled
    ``create_agent`` graph (LangChain ``BaseChatModel`` + MCP tools).
    """

    __slots__ = ("agent_builder", "_max_iterations", "_agent")

    def __init__(self, agent_builder: AgentBuilder, *, max_iterations: int = MAX_SYNTHESIS_ITERATIONS) -> None:
        """Create a AgentSynthesizeNode.

        Args:
            agent_builder: Agent builder.
            max_iterations: Maximum iterations.
        """
        self.agent_builder = agent_builder
        self._max_iterations = max_iterations
        self._agent: Any = None  # lazy: compiled create_agent graph (Any — betalpha API)

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        agent = await self._get_agent()
        prompt = self._build_prompt(state)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        last = result["messages"][-1]
        notes = getattr(last, "content", "") or ""
        return {"notes": [notes], "iterations": state.get("iterations", 0) + 1}

    async def _get_agent(self) -> Any:
        """Get agent.

        Returns:
            Any: Result.
        """
        if self._agent is None:
            self._agent = await self.agent_builder()
        return self._agent

    @staticmethod
    def _build_prompt(state: WorkflowState) -> str:
        """Build prompt.

        Args:
            state: Current workflow state.

        Returns:
            str: Result text.
        """
        bodies = sorted(_coerce_bodies(state), key=lambda b: str(b.url or b.title or ""))
        ctx = "\n".join((b.title or b.url) + "\n" + (b.markdown or "")[:3000] for b in bodies)
        return f"Research question: {state.get('query', '')}\n\nAvailable evidence:\n{ctx}"


def _coerce_bodies(state: WorkflowState) -> list[DocumentBody]:
    """Accept DocumentBody objects or their raw dict snapshots (LangGraph state).

    Args:
        state: Current workflow state.

    Returns:
        list[DocumentBody]: Document bodies.

    """
    out: list[DocumentBody] = []
    for b in state.get("bodies", []):
        out.append(b if isinstance(b, DocumentBody) else DocumentBody(**b))
    return out


__all__ = ["AgentBuilder", "AgentSynthesizeNode", "_AGENT_SYSTEM", "Affinity", "ChatRequest"]

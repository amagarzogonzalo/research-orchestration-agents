"""Workflow registry — multiple workflows = multiple graphs (ADR 0008 §5).

One set of :class:`~knowledge_engine.orchestration.state.WorkflowNode` classes
composes into several compiled :class:`~langgraph.graph.state.StateGraph` graphs
(research, qa, summary) that share node classes but wire different topologies.
Every graph gets graph-wide ``retry_policy`` / ``timeout`` / ``error_handler``
defaults via :meth:`StateGraph.set_node_defaults` and the same checkpointer.
"""

from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send, TimeoutPolicy

from knowledge_engine.orchestration.guardrails import should_stop
from knowledge_engine.orchestration.nodes import (
    FETCH_NODE,
    JUDGE_NODE,
    RENDER_NODE,
    REVIEW_NODE,
    SYNTHESIZE_NODE,
    judge_route,
    review_route,
)
from knowledge_engine.orchestration.nodes.agent import AgentSynthesizeNode
from knowledge_engine.orchestration.nodes.aggregate import AGGREGATE_NODE, AggregateNode
from knowledge_engine.orchestration.nodes.bodies import BodiesNode, FetchBodyNode
from knowledge_engine.orchestration.nodes.branch import BRANCH_NODE, BranchNode
from knowledge_engine.orchestration.nodes.critic import CRITIC_NODE, CriticNode
from knowledge_engine.orchestration.nodes.decompose import DECOMPOSE_NODE, DecomposeNode
from knowledge_engine.orchestration.nodes.ingest import IngestNode
from knowledge_engine.orchestration.nodes.intent import INTENT_NODE, IntentNode
from knowledge_engine.orchestration.nodes.judge import JudgeNode
from knowledge_engine.orchestration.nodes.relevance import RelevanceNode
from knowledge_engine.orchestration.nodes.render import RenderNode
from knowledge_engine.orchestration.nodes.review import ReviewNode
from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode
from knowledge_engine.orchestration.state import WorkflowState
from knowledge_engine.schemas.decomposition import IntentKind

logger = getLogger(__name__)

# Graph-wide fault-tolerance defaults (ADR 0008 §2.4) — transient failures are
# retried with backoff; a node that exhausts retries compensates via handler.
# Note: idle_timeout must comfortably exceed a real LLM call (Gemini/OpenRouter
# can take 30-90s to synthesize); a 30s idle budget killed live runs.
_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0)
_TIMEOUT = TimeoutPolicy(run_timeout=300, idle_timeout=120)


@dataclass(frozen=True)
class NodeSet:
    """Immutable bundle of dependency-injected node instances (composition root).

    Attributes:
        ingest: Ingest node.
        relevance: Relevance node.
        bodies: Bodies node.
        fetch_body: Fetch-body node.
        synthesize: Synthesize node.
        judge: Judge node.
        review: Review node.
        render: Render node.
    """

    ingest: IngestNode
    relevance: RelevanceNode
    bodies: BodiesNode
    fetch_body: FetchBodyNode
    synthesize: object  # SynthesizeNode | AgentSynthesizeNode
    judge: JudgeNode
    review: ReviewNode
    render: RenderNode
    intent: IntentNode | None = None
    decompose: DecomposeNode | None = None
    branch: BranchNode | None = None
    aggregate: AggregateNode | None = None
    critic: CriticNode | None = None


async def _error_handler(state: WorkflowState, error: NodeError) -> dict[str, Any]:
    """Compensation: mark the run failed instead of aborting the thread.

    Args:
        state: Current workflow state.
        error: Node error.

    Returns:
        dict[str, Any]: Partial state marking the run failed.
    """
    logger.error("node %s failed: %s", error.node, error.error)
    return {"status": "failed", "judge_critique": f"failed at {error.node}: {error.error}"}


def build_workflow(
    name: str,
    nodes: NodeSet,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: Any | None = None,
) -> Any:
    """Build and compile the requested workflow graph.

    Args:
        name: Workflow name.
        nodes: Node set.
        checkpointer: Durability checkpointer.
        store: Cross-thread memory store (LangGraph ``BaseStore``).

    Returns:
        Any: Compiled graph.

    Raises:
        ValueError: When the workflow name is unknown.
    """
    builder = {"research": _build_research, "qa": _build_qa, "summary": _build_summary, "deep": _build_deep}.get(name)
    if builder is None:
        raise ValueError(f"unknown workflow: {name!r}")
    return builder(nodes, checkpointer=checkpointer, store=store)


def _base() -> StateGraph[Any, Any, Any, Any]:
    """Create a state graph with fault-tolerance defaults.

    Returns:
        StateGraph[Any, Any, Any, Any]: Configured graph builder.
    """
    g = StateGraph(WorkflowState)
    # error_handler must be an async node (timeouts are async-only in langgraph).
    g.set_node_defaults(retry_policy=_RETRY, timeout=_TIMEOUT, error_handler=_error_handler)  # type: ignore[arg-type]
    return g


def _register(g: StateGraph[Any, Any, Any, Any], nodes: NodeSet) -> None:
    """Register nodes on a graph builder.

    Args:
        g: Graph builder.
        nodes: Node set.
    """
    g.add_node("ingest", nodes.ingest)
    g.add_node("relevance", nodes.relevance)
    g.add_node("bodies", nodes.bodies)
    g.add_node(FETCH_NODE, nodes.fetch_body)
    g.add_node(SYNTHESIZE_NODE, nodes.synthesize)  # type: ignore[call-overload]
    g.add_node("judge", nodes.judge)
    g.add_node("review", nodes.review)
    g.add_node("render", nodes.render)


def _build_research(
    nodes: NodeSet, *, checkpointer: BaseCheckpointSaver[Any] | None = None, store: Any | None = None
) -> Any:
    """Build the research workflow graph.

    Args:
        nodes: Node set.
        checkpointer: Durability checkpointer.
        store: Cross-thread memory store.

    Returns:
        Any: Compiled research graph.
    """
    g = _base()
    _register(g, nodes)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "relevance")
    g.add_edge("relevance", "bodies")
    g.add_edge(FETCH_NODE, SYNTHESIZE_NODE)
    g.add_edge(SYNTHESIZE_NODE, "judge")
    g.add_conditional_edges("judge", judge_route, {SYNTHESIZE_NODE: SYNTHESIZE_NODE, REVIEW_NODE: REVIEW_NODE})
    g.add_conditional_edges("review", review_route, {RENDER_NODE: RENDER_NODE, END: END})
    g.add_edge(RENDER_NODE, END)
    return g.compile(checkpointer=checkpointer, store=store)


def _build_qa(
    nodes: NodeSet, *, checkpointer: BaseCheckpointSaver[Any] | None = None, store: Any | None = None
) -> Any:
    """Q&A: no body fan-out, no judge loop — answer from abstracts directly.

    Args:
        nodes: Node set.
        checkpointer: Durability checkpointer.
        store: Cross-thread memory store.

    Returns:
        Any: Compiled QA graph.
    """
    g = _base()
    _register(g, nodes)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "relevance")
    g.add_edge("relevance", SYNTHESIZE_NODE)
    g.add_edge(SYNTHESIZE_NODE, RENDER_NODE)
    g.add_edge(RENDER_NODE, END)
    return g.compile(checkpointer=checkpointer, store=store)


def _build_summary(
    nodes: NodeSet, *, checkpointer: BaseCheckpointSaver[Any] | None = None, store: Any | None = None
) -> Any:
    """Summary: ingest + bodies + synthesize (no relevance gate, no judge).

    Args:
        nodes: Node set.
        checkpointer: Durability checkpointer.
        store: Cross-thread memory store.

    Returns:
        Any: Compiled summary graph.
    """
    g = _base()
    _register(g, nodes)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "bodies")
    g.add_edge(FETCH_NODE, SYNTHESIZE_NODE)
    g.add_edge(SYNTHESIZE_NODE, RENDER_NODE)
    g.add_edge(RENDER_NODE, END)
    return g.compile(checkpointer=checkpointer, store=store)


def _require_deep(nodes: NodeSet) -> NodeSet:
    """Return nodes or raise when the deep set is incomplete.

    Args:
        nodes: Node set.

    Returns:
        NodeSet: The same node set.

    Raises:
        ConfigurationError: When any deep node is missing.
    """
    from knowledge_engine.core.exceptions import ConfigurationError

    missing = [n for n in ("intent", "decompose", "branch", "aggregate", "critic") if getattr(nodes, n) is None]
    if missing:
        raise ConfigurationError(f"deep workflow needs nodes: {', '.join(missing)}")
    return nodes


def intent_route(state: WorkflowState) -> str:
    """Route after intent: deep enters the loop, the rest answer in one pass.

    Args:
        state: Current workflow state.

    Returns:
        str: Next node name.
    """
    if state.get("intent", IntentKind.DEEP.value) == IntentKind.DEEP.value:
        return DECOMPOSE_NODE
    return SYNTHESIZE_NODE


def dispatch(state: WorkflowState) -> Any:
    """Fan out one Send per planned sub-query (empty plan fails safe to judge).

    Args:
        state: Current workflow state.

    Returns:
        Any: Send list or the judge node name.
    """
    sends = [Send(BRANCH_NODE, {"sub_query": sq}) for sq in state.get("sub_queries", []) if sq.get("query")]
    return sends or JUDGE_NODE


def deep_route(state: WorkflowState) -> str:
    """Route after aggregate: replan on gaps, else critic → judge.

    Args:
        state: Current workflow state.

    Returns:
        str: Next node name.
    """
    if should_stop(
        score=state.get("rubric_score") or 0.0,
        gaps=state.get("gaps", []),
        depth=state.get("depth", 0),
        rounds=state.get("rounds", 0),
        llm_calls=state.get("llm_calls", 0),
    ):
        return CRITIC_NODE
    return DECOMPOSE_NODE


def synthesize_route(state: WorkflowState) -> str:
    """Route after synthesize: cheap intents render, deep refines via judge.

    Args:
        state: Current workflow state.

    Returns:
        str: Next node name.
    """
    if state.get("intent", IntentKind.DEEP.value) == IntentKind.DEEP.value:
        return JUDGE_NODE
    return RENDER_NODE


def _build_deep(
    nodes: NodeSet, *, checkpointer: BaseCheckpointSaver[Any] | None = None, store: Any | None = None
) -> Any:
    """Deep: recursive planner over homogeneous branch workers + rubric gate.

    Args:
        nodes: Node set (must include the deep nodes).
        checkpointer: Durability checkpointer.
        store: Cross-thread memory store.

    Returns:
        Any: Compiled deep graph.
    """
    _require_deep(nodes)
    g = _base()
    _register(g, nodes)
    g.add_node(INTENT_NODE, nodes.intent)  # type: ignore[arg-type]
    g.add_node(DECOMPOSE_NODE, nodes.decompose)  # type: ignore[arg-type]
    g.add_node(BRANCH_NODE, nodes.branch)  # type: ignore[arg-type]
    g.add_node(AGGREGATE_NODE, nodes.aggregate)  # type: ignore[arg-type]
    g.add_node(CRITIC_NODE, nodes.critic)  # type: ignore[arg-type]
    g.add_edge(START, INTENT_NODE)
    g.add_conditional_edges(INTENT_NODE, intent_route, [DECOMPOSE_NODE, SYNTHESIZE_NODE])
    g.add_conditional_edges(DECOMPOSE_NODE, dispatch, [BRANCH_NODE, JUDGE_NODE])
    g.add_edge(BRANCH_NODE, AGGREGATE_NODE)
    g.add_conditional_edges(AGGREGATE_NODE, deep_route, [DECOMPOSE_NODE, CRITIC_NODE])
    g.add_edge(CRITIC_NODE, JUDGE_NODE)
    g.add_conditional_edges(JUDGE_NODE, judge_route, [SYNTHESIZE_NODE, REVIEW_NODE])
    g.add_conditional_edges(REVIEW_NODE, review_route, [RENDER_NODE, END])
    g.add_conditional_edges(SYNTHESIZE_NODE, synthesize_route, [JUDGE_NODE, RENDER_NODE])
    g.add_edge(RENDER_NODE, END)
    return g.compile(checkpointer=checkpointer, store=store)


__all__ = [
    "END",
    "AgentSynthesizeNode",
    "AggregateNode",
    "BodiesNode",
    "BranchNode",
    "CriticNode",
    "DecomposeNode",
    "FetchBodyNode",
    "IngestNode",
    "IntentNode",
    "JudgeNode",
    "NodeSet",
    "RelevanceNode",
    "RenderNode",
    "ReviewNode",
    "SynthesizeNode",
    "build_workflow",
    "deep_route",
    "dispatch",
    "intent_route",
    "synthesize_route",
]

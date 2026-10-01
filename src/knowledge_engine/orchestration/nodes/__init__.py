"""SOLID node classes, one file per node (ADR 0008 §4).

Nodes are immutable, dependency-injected, and stateless per run: they read the
raw :class:`~knowledge_engine.orchestration.state.WorkflowState`, do one bounded
unit of work against injected ports/providers, and return partial state writes
or a :class:`langgraph.types.Command` for explicit routing.
"""

from __future__ import annotations

from knowledge_engine.orchestration.nodes.agent import AgentSynthesizeNode
from knowledge_engine.orchestration.nodes.bodies import FETCH_NODE, JOIN_NODE, BodiesNode, FetchBodyNode
from knowledge_engine.orchestration.nodes.ingest import IngestNode
from knowledge_engine.orchestration.nodes.judge import REVIEW_NODE, SYNTHESIZE_NODE, JudgeNode, judge_route
from knowledge_engine.orchestration.nodes.relevance import RelevanceNode
from knowledge_engine.orchestration.nodes.render import RenderNode
from knowledge_engine.orchestration.nodes.review import END, RENDER_NODE, ReviewNode, review_route
from knowledge_engine.orchestration.nodes.synthesize import SynthesizeNode

__all__ = [
    "AgentSynthesizeNode",
    "BodiesNode",
    "END",
    "FETCH_NODE",
    "FetchBodyNode",
    "IngestNode",
    "JOIN_NODE",
    "JudgeNode",
    "RENDER_NODE",
    "REVIEW_NODE",
    "RelevanceNode",
    "RenderNode",
    "ReviewNode",
    "SYNTHESIZE_NODE",
    "SynthesizeNode",
    "judge_route",
    "review_route",
]

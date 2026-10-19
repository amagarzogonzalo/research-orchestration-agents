"""Decomposition models: recursive research plans over homogeneous workers.

A master question becomes a :class:`ResearchPlan` of :class:`SubQuery` items,
each tagged with a retrieval ``source`` (search/scrape/mcp/browser) and a
``depth``. Root/child/grandchild runs share one worker implementation and
differ only in identity, depth, and budget (PaperPilot homogeneous recursion).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_DECOMPOSE_DEPTH = 3
"""Deepest sub-query depth (root plans depth-1 workers; they may plan depth-2/3)."""

MAX_SUBQUERIES = 7
"""Fan-out cap per decomposition round (shared-budget discipline)."""

MAX_DEEP_ROUNDS = 3
"""Replan-loop cap: decompose → branches → aggregate may repeat this often."""

MAX_BRANCH_LLM_CALLS = 12
"""Shared LLM-call budget across one deep run (all rounds, all branches)."""

RUBRIC_THRESHOLD = 0.7
"""Evidence-sufficiency score at which the rubric gate stops researching."""

MIN_DEEP_SOURCES = 8
"""Merged source count the deep rubric must reach before an early stop is allowed."""


class SourceKind(StrEnum):
    """Retrieval route for one sub-query.

    Attributes:
        SEARCH: Federated scholarly search.
        SCRAPE: URL-to-markdown fetch of known sources.
        MCP: Model Context Protocol tool sources.
        BROWSER: Full browser automation for dynamic pages.
    """

    SEARCH = "search"
    SCRAPE = "scrape"
    MCP = "mcp"
    BROWSER = "browser"


class IntentKind(StrEnum):
    """Unified-entry intent (PaperPilot conversation orchestration).

    Attributes:
        CHAT: Plain conversational reply.
        MEMORY: Current-memory question answering.
        QUICK: Fast retrieval over few sources.
        DEEP: Full recursive research proposal.
        UNDECIDED: Heuristic abstains; the intent node asks the LLM.
    """

    CHAT = "chat"
    MEMORY = "memory"
    QUICK = "quick"
    DEEP = "deep"
    UNDECIDED = "undecided"


class SubQuery(BaseModel):
    """One branch of a research plan.

    Attributes:
        model_config: Attribute description.
        query: Attribute description.
        source: Attribute description.
        rationale: Attribute description.
        depth: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    source: SourceKind = SourceKind.SEARCH
    rationale: str = ""
    depth: int = Field(default=0, ge=0)


class ResearchPlan(BaseModel):
    """A decomposition round: sub-queries sharing one parent depth.

    Attributes:
        model_config: Attribute description.
        sub_queries: Attribute description.
        strategy: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    sub_queries: list[SubQuery] = Field(default_factory=list)
    strategy: str = ""


class CriticVerdict(StrEnum):
    """Critic verdict on the merged evidence.

    Attributes:
        SUFFICIENT: Evidence supports a report.
        REPLAN: Named gaps require another round.
    """

    SUFFICIENT = "sufficient"
    REPLAN = "replan"


class CriticReport(BaseModel):
    """Structured rubric feedback from the critic (advises; the judge decides).

    Attributes:
        model_config: Attribute description.
        verdict: Attribute description.
        coverage: Attribute description.
        diversity: Attribute description.
        grounding: Attribute description.
        contradictions: Attribute description.
        unanswered: Attribute description.
        revision_guidance: Attribute description.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: CriticVerdict = CriticVerdict.SUFFICIENT
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding: float = Field(default=0.0, ge=0.0, le=1.0)
    contradictions: list[str] = Field(default_factory=list)
    unanswered: list[str] = Field(default_factory=list)
    revision_guidance: str = ""


__all__ = [
    "CriticReport",
    "CriticVerdict",
    "IntentKind",
    "MAX_BRANCH_LLM_CALLS",
    "MAX_DECOMPOSE_DEPTH",
    "MAX_DEEP_ROUNDS",
    "MAX_SUBQUERIES",
    "MIN_DEEP_SOURCES",
    "RUBRIC_THRESHOLD",
    "ResearchPlan",
    "SourceKind",
    "SubQuery",
]

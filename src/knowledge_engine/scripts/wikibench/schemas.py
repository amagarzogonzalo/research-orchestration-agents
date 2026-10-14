"""WikiBench data contracts: query cases, suite results and the report.

Deterministic by construction — no timestamps, no randomness — so a report is
a pure function of (corpus seed, backend, tier, scale) and ``--check`` can diff
two runs byte-for-byte.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class QueryKind(StrEnum):
    """How a benchmark query relates to the corpus.

    Attributes:
        EXACT: The page title verbatim.
        PARAPHRASE: Rewritten terms still answerable by one page.
        ALIAS: An entity-variant/unique marker for one page.
        MULTI_PAGE: Spans two or more pages.
        UNANSWERABLE: Shares no term with any page (a miss is correct).
    """

    EXACT = "exact"
    PARAPHRASE = "paraphrase"
    ALIAS = "alias"
    MULTI_PAGE = "multi_page"
    UNANSWERABLE = "unanswerable"


class QueryCase(BaseModel):
    """One benchmark query with its gold pages.

    Attributes:
        model_config: Forbid extra keys.
        id: Stable case id (suite prefix + index).
        suite: Suite name the case belongs to.
        kind: Query kind.
        query: The query text.
        gold_slugs: Slugs a correct retrieval must return (empty = miss).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    suite: str
    kind: QueryKind
    query: str
    gold_slugs: list[str] = Field(default_factory=list)


class CorpusPage(BaseModel):
    """One seeded fictional page.

    Attributes:
        model_config: Forbid extra keys.
        slug: Kebab slug (unique).
        title: Page title (unique).
        marker: Unique token embedded in the body (alias/anchor).
        body: Full markdown body.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    marker: str
    body: str


class SuiteResult(BaseModel):
    """One suite's measured outcome.

    Attributes:
        model_config: Forbid extra keys.
        name: Suite name (e.g. ``a_retrieval``).
        backend: Backend id (``file``).
        tier: Execution tier (``0``).
        scale: Corpus size used.
        metrics: Metric name -> value.
        passed: Whether every hard threshold for this suite passed.
        findings: Human-readable failures (empty when clean).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    backend: str
    tier: str
    scale: int
    metrics: dict[str, float] = Field(default_factory=dict)
    passed: bool = True
    findings: list[str] = Field(default_factory=list)


class BenchReport(BaseModel):
    """A full WikiBench run.

    Attributes:
        model_config: Forbid extra keys.
        backend: Backend id.
        tier: Execution tier.
        scale: Corpus size.
        seed: Corpus seed.
        suites: Per-suite results.
    """

    model_config = ConfigDict(extra="forbid")

    backend: str
    tier: str
    scale: int
    seed: int
    suites: list[SuiteResult] = Field(default_factory=list)

    def metrics(self) -> dict[str, float]:
        """Flatten every suite metric into ``suite.metric`` keys.

        Returns:
            dict[str, float]: Flattened metrics.
        """
        flat: dict[str, float] = {}
        for result in self.suites:
            for metric, value in result.metrics.items():
                flat[f"{result.name}.{metric}"] = value
        return flat


__all__ = ["BenchReport", "CorpusPage", "QueryCase", "QueryKind", "SuiteResult"]
